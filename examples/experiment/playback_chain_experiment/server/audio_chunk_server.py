#!/usr/bin/env python3
"""离线音频分片实验服务。

测试目标：把仓库中的离线 WAV 转成固定时长的 PCM chunk，供 iOS 真机按水位线拉取。
测试方法：启动 HTTP 服务，创建播放 session 后按 after_seq/limit 查询 chunk。
预期结果：iOS 端可以通过暂停和恢复拉取，验证本地播放 buffer 的水位线控制。
"""

from __future__ import annotations

import argparse
import base64
import json
import threading
import uuid
import wave
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pcm16_utils import pcm16_to_mono, resample_pcm16_mono


@dataclass(frozen=True)
class AudioFormat:
    """实验音频格式。"""

    codec: str
    sample_rate: int
    channels: int
    chunk_ms: int


@dataclass(frozen=True)
class AudioChunk:
    """一片可拉取的音频 chunk。"""

    seq: int
    payload: bytes
    duration_ms: int
    final: bool


@dataclass
class AudioSession:
    """一次播放实验 session。"""

    session_id: str
    scenario: str
    repeat: int
    cancelled: bool = False
    client_last_received_seq: int | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


class AudioRepository:
    """离线音频仓库。

    主要功能：启动时读取 WAV，转换成统一 PCM16LE mono，并切成固定时长 chunk。
    """

    def __init__(self, audio_path: Path, sample_rate: int, chunk_ms: int) -> None:
        self.format = AudioFormat(codec="pcm16le", sample_rate=sample_rate, channels=1, chunk_ms=chunk_ms)
        self.chunks = self._load_chunks(audio_path)

    @property
    def total_duration_ms(self) -> int:
        return sum(chunk.duration_ms for chunk in self.chunks)

    def _load_chunks(self, audio_path: Path) -> list[AudioChunk]:
        with wave.open(str(audio_path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            source_rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
        if sample_width != 2:
            raise ValueError(f"只支持 16-bit PCM WAV，当前 sample_width={sample_width}")
        if channels != 1:
            frames = pcm16_to_mono(frames, channels)
        if source_rate != self.format.sample_rate:
            frames = resample_pcm16_mono(frames, source_rate, self.format.sample_rate)
        bytes_per_chunk = max(1, int(self.format.sample_rate * self.format.chunk_ms / 1000) * sample_width)
        chunks: list[AudioChunk] = []
        for seq, offset in enumerate(range(0, len(frames), bytes_per_chunk)):
            payload = frames[offset : offset + bytes_per_chunk]
            if not payload:
                continue
            duration_ms = int(round((len(payload) / sample_width) * 1000 / self.format.sample_rate))
            chunks.append(AudioChunk(seq=seq, payload=payload, duration_ms=duration_ms, final=False))
        if chunks:
            last = chunks[-1]
            chunks[-1] = AudioChunk(seq=last.seq, payload=last.payload, duration_ms=last.duration_ms, final=True)
        return chunks


class PlaybackChunkStore:
    """实验 session 状态存储。"""

    def __init__(self, repository: AudioRepository) -> None:
        self.repository = repository
        self._lock = threading.Lock()
        self._sessions: dict[str, AudioSession] = {}

    def create_session(self, scenario: str, repeat: int) -> AudioSession:
        session = AudioSession(
            session_id=f"audio_sess_{uuid.uuid4().hex[:12]}",
            scenario=scenario or "default",
            repeat=max(1, repeat),
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> AudioSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def chunks_after(self, session_id: str, after_seq: int, limit: int) -> tuple[list[AudioChunk], bool]:
        session = self.get_session(session_id)
        if session.cancelled:
            return [], True
        start = max(0, after_seq + 1)
        end = min(len(self.repository.chunks), start + max(1, min(limit, 128)))
        chunks = self.repository.chunks[start:end]
        finished = end >= len(self.repository.chunks)
        with self._lock:
            session.events.append({"event": "chunks_requested", "after_seq": after_seq, "limit": limit, "returned": len(chunks)})
        return chunks, finished

    def cancel(self, session_id: str, client_last_received_seq: int | None, reason: str) -> AudioSession:
        session = self.get_session(session_id)
        with self._lock:
            session.cancelled = True
            session.client_last_received_seq = client_last_received_seq
            session.events.append({"event": "cancelled", "reason": reason, "client_last_received_seq": client_last_received_seq})
        return session


class AudioChunkHandler(BaseHTTPRequestHandler):
    """HTTP API 处理器。"""

    store: PlaybackChunkStore

    def do_GET(self) -> None:
        """处理健康检查和 chunk 拉取。"""

        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json({"ok": True})
            return
        if parsed.path.startswith("/audio/sessions/") and parsed.path.endswith("/chunks"):
            self._handle_get_chunks(parsed.path, parse_qs(parsed.query))
            return
        self.send_error(404)

    def do_POST(self) -> None:
        """处理 session 创建和取消。"""

        parsed = urlparse(self.path)
        if parsed.path == "/audio/sessions":
            self._handle_create_session()
            return
        if parsed.path.startswith("/audio/sessions/") and parsed.path.endswith("/cancel"):
            self._handle_cancel(parsed.path)
            return
        self.send_error(404)

    def log_message(self, format: str, *args: Any) -> None:
        """打印简洁请求日志。"""

        print(f"{self.address_string()} {format % args}")

    def _handle_create_session(self) -> None:
        payload = self._read_json()
        session = self.store.create_session(
            scenario=str(payload.get("scenario") or "default"),
            repeat=int(payload.get("repeat") or 1),
        )
        audio_format = self.store.repository.format
        self._send_json(
            {
                "ok": True,
                "session_id": session.session_id,
                "format": audio_format.__dict__,
                "total_chunks": len(self.store.repository.chunks),
                "total_duration_ms": self.store.repository.total_duration_ms,
            }
        )

    def _handle_get_chunks(self, path: str, query: dict[str, list[str]]) -> None:
        session_id = self._session_id_from_path(path, suffix="/chunks")
        after_seq = int((query.get("after_seq") or ["-1"])[0])
        limit = int((query.get("limit") or ["16"])[0])
        try:
            chunks, finished = self.store.chunks_after(session_id, after_seq=after_seq, limit=limit)
        except KeyError:
            self._send_json({"ok": False, "error": f"unknown session: {session_id}"}, status=404)
            return
        self._send_json(
            {
                "ok": True,
                "session_id": session_id,
                "chunks": [self._chunk_payload(chunk) for chunk in chunks],
                "next_seq": chunks[-1].seq + 1 if chunks else after_seq + 1,
                "server_finished": finished,
            }
        )

    def _handle_cancel(self, path: str) -> None:
        session_id = self._session_id_from_path(path, suffix="/cancel")
        payload = self._read_json()
        last_seq = payload.get("client_last_received_seq")
        try:
            session = self.store.cancel(
                session_id,
                client_last_received_seq=int(last_seq) if last_seq is not None else None,
                reason=str(payload.get("reason") or "client_cancelled"),
            )
        except KeyError:
            self._send_json({"ok": False, "error": f"unknown session: {session_id}"}, status=404)
            return
        self._send_json({"ok": True, "session_id": session.session_id, "state": "cancelled"})

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        data = self.rfile.read(length)
        return json.loads(data.decode("utf-8"))

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _session_id_from_path(self, path: str, suffix: str) -> str:
        prefix = "/audio/sessions/"
        return path[len(prefix) : -len(suffix)]

    def _chunk_payload(self, chunk: AudioChunk) -> dict[str, Any]:
        return {
            "seq": chunk.seq,
            "duration_ms": chunk.duration_ms,
            "payload_base64": base64.b64encode(chunk.payload).decode("ascii"),
            "final": chunk.final,
        }


def main() -> None:
    """启动音频分片服务。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8778)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--sample-rate", type=int, default=24000)
    parser.add_argument("--chunk-ms", type=int, default=20)
    args = parser.parse_args()

    repository = AudioRepository(Path(args.audio), sample_rate=args.sample_rate, chunk_ms=args.chunk_ms)
    AudioChunkHandler.store = PlaybackChunkStore(repository)
    server = ThreadingHTTPServer((args.host, args.port), AudioChunkHandler)
    print(
        "Audio chunk server listening on "
        f"http://{args.host}:{args.port} chunks={len(repository.chunks)} "
        f"sample_rate={repository.format.sample_rate} chunk_ms={repository.format.chunk_ms}"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
