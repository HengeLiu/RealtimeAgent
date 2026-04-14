#!/usr/bin/env python3
"""更简单的眼镜侧语音模拟脚本。"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="简单眼镜端语音模拟脚本")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--device-id", default="glass-001")
    parser.add_argument("--pair-token", default="pair-demo-token")
    parser.add_argument("--wav", required=True, help="本地 16kHz/mono/16bit wav 文件")
    parser.add_argument(
        "--save-reply",
        default="runs/simple-glass-replies",
        help="保存服务器返回音频的相对路径；可传目录或 wav 文件名，最终文件名会自动追加时间戳",
    )
    parser.add_argument("--timeout-seconds", type=float, default=45.0, help="网络超时时间，单位秒")
    parser.add_argument("--chunk-interval-ms", type=int, default=20, help="每个音频包的发送间隔，单位毫秒")
    return parser.parse_args()


class WsClient:
    """最小 WebSocket 客户端。"""

    def __init__(self, host: str, port: int, path: str, *, timeout_seconds: float) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout_seconds)
        self.sock.settimeout(timeout_seconds)
        self._handshake(host, port, path)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def send_binary(self, payload: bytes) -> None:
        self._send_frame(0x2, payload)

    def recv_text(self) -> str:
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            payload = self._recv_exact(length)
            if opcode == 0x1:
                return payload.decode("utf-8")
            if opcode == 0x8:
                raise ConnectionError("websocket closed")

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        head = bytearray()
        head.append(0x80 | opcode)
        length = len(payload)
        mask_key = os.urandom(4)
        if length < 126:
            head.append(0x80 | length)
        elif length < 65536:
            head.append(0x80 | 126)
            head.extend(struct.pack("!H", length))
        else:
            head.append(0x80 | 127)
            head.extend(struct.pack("!Q", length))
        masked = bytes(value ^ mask_key[index % 4] for index, value in enumerate(payload))
        self.sock.sendall(bytes(head) + mask_key + masked)

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("utf-8")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("utf-8"))
        response = self._recv_until(b"\r\n\r\n").decode("utf-8")
        if "101 Switching Protocols" not in response:
            raise RuntimeError(f"websocket handshake failed: {response}")

    def _recv_until(self, marker: bytes) -> bytes:
        data = bytearray()
        while marker not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("socket closed")
            data.extend(chunk)
        return bytes(data)

    def _recv_exact(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("socket closed")
            data.extend(chunk)
        return bytes(data)


def now_ms() -> int:
    """返回当前毫秒时间戳。"""

    return int(time.time() * 1000)


def build_endpoint(device_id: str, device_type: str, module: str) -> dict[str, str]:
    """构造协议端点。"""

    return {"device_id": device_id, "device_type": device_type, "module": module}


def build_message(
    name: str,
    semantic: str,
    source: dict[str, str],
    target: dict[str, str],
    payload: dict[str, object],
    *,
    session_id: str | None = None,
    stream_id: str | None = None,
) -> str:
    """构造控制消息 JSON。"""

    message = {
        "version": "v1",
        "message_id": f"msg_{now_ms()}_{os.urandom(4).hex()}",
        "channel": "control",
        "semantic": semantic,
        "name": name,
        "source": source,
        "target": target,
        "ts": now_ms(),
        "payload": payload,
        "meta": {},
    }
    if session_id:
        message["session_id"] = session_id
    if stream_id:
        message["stream_id"] = stream_id
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def load_pcm_chunks(path: str) -> list[bytes]:
    """读取标准 wav 并按 20ms 切片。"""

    with wave.open(path, "rb") as wav_file:
        if wav_file.getframerate() != 16000 or wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
            raise ValueError("仅支持 16kHz/mono/16bit wav；若输入是 m4a，请先执行 audio-sample/convert_audio_samples.py")
        data = wav_file.readframes(wav_file.getnframes())
    return [data[offset : offset + 640] for offset in range(0, len(data), 640) if data[offset : offset + 640]]


def build_media_frame(stream_id: str, segment_id: str, seq: int, payload: bytes) -> bytes:
    """构造音频帧。"""

    header = {
        "version": "v1",
        "stream_id": stream_id,
        "segment_id": segment_id,
        "frame_type": "audio_chunk",
        "seq": seq,
        "ts_ms": now_ms(),
        "codec": "pcm16le",
        "sample_rate": 16000,
        "channels": 1,
        "payload_size": len(payload),
        "final": False,
    }
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    return len(header_bytes).to_bytes(4, "big") + header_bytes + payload


def repo_root() -> Path:
    """返回当前项目根目录。"""

    return Path(__file__).resolve().parents[1]


def build_reply_output_path(save_reply: str, input_wav: str) -> Path:
    """构造回复音频输出路径。

    主要逻辑：
    1. 相对路径一律基于当前项目根目录解析。
    2. 若传入目录，则使用输入音频名作为输出基名。
    3. 最终文件名统一追加时间戳，避免覆盖旧结果。
    """

    project_root = repo_root()
    input_stem = Path(input_wav).stem
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    raw_target = Path(save_reply)
    target_path = raw_target if raw_target.is_absolute() else project_root / raw_target

    if target_path.suffix.lower() == ".wav":
        output_dir = target_path.parent
        base_name = target_path.stem
    else:
        output_dir = target_path
        base_name = input_stem

    return output_dir / f"{base_name}_{timestamp}.wav"


@dataclass(slots=True)
class HeartbeatWorker:
    """后台心跳发送器。"""

    control: WsClient
    source: dict[str, str]
    target: dict[str, str]
    device_id: str
    interval_ms: int
    stop_event: threading.Event

    def run(self) -> None:
        """定时发送心跳。"""

        interval_s = max(self.interval_ms / 1000, 0.1)
        while not self.stop_event.wait(interval_s):
            try:
                self.control.send_text(
                    build_message(
                        "device.heartbeat",
                        "notify",
                        self.source,
                        self.target,
                        {"device_id": self.device_id},
                    )
                )
            except (OSError, ConnectionError):
                return


def wait_for_reply(control: WsClient) -> tuple[dict[str, object], dict[str, object]]:
    """等待服务端返回文本和播放指令。"""

    assistant_reply: dict[str, object] | None = None
    audio_play: dict[str, object] | None = None

    while assistant_reply is None or audio_play is None:
        message = json.loads(control.recv_text())
        if message["name"] == "assistant.reply":
            assistant_reply = message
            continue
        if message["name"] == "actuator.audio.play":
            audio_play = message
            continue
    return assistant_reply, audio_play


def main() -> None:
    """脚本主入口。"""

    args = parse_args()
    control = WsClient(args.host, args.port, "/ws/control", timeout_seconds=args.timeout_seconds)
    source = build_endpoint(args.device_id, "glass", "glass-api")
    target = build_endpoint("server-main", "server", "server-api")
    heartbeat_stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None

    try:
        control.send_text(
            build_message(
                "device.register",
                "request",
                source,
                target,
                {
                    "device_id": args.device_id,
                    "device_type": "glass",
                    "firmware_version": "simple-sim",
                    "auth": {"mode": "pair_token", "pair_token": args.pair_token},
                },
            )
        )
        registered = json.loads(control.recv_text())
        if registered["name"] != "device.registered":
            raise RuntimeError(f"unexpected register response: {registered}")
        print(f"registered: {registered['payload']['device_id']}")
        heartbeat_interval_ms = int(registered["payload"].get("heartbeat_interval_ms", 5000))

        opened = json.loads(control.recv_text())
        if opened["name"] != "voice.session.open":
            raise RuntimeError(f"unexpected open response: {opened}")
        session_id = opened["session_id"]
        print(f"voice_session_open: {session_id}")

        control.send_text(
            build_message(
                "voice.session.opened",
                "notify",
                source,
                target,
                {"device_id": args.device_id},
                session_id=session_id,
            )
        )

        heartbeat_thread = threading.Thread(
            target=HeartbeatWorker(
                control=control,
                source=source,
                target=target,
                device_id=args.device_id,
                interval_ms=heartbeat_interval_ms,
                stop_event=heartbeat_stop,
            ).run,
            name="simple-glass-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()

        audio = WsClient(
            args.host,
            args.port,
            f"/ws_audio?{urlencode({'device_id': args.device_id})}",
            timeout_seconds=args.timeout_seconds,
        )
        try:
            stream_id = f"stream_{os.urandom(4).hex()}"
            segment_id = f"seg_{os.urandom(4).hex()}"
            chunks = load_pcm_chunks(args.wav)

            control.send_text(
                build_message(
                    "sensor.audio.segment.started",
                    "notify",
                    source,
                    target,
                    {
                        "device_id": args.device_id,
                        "stream_id": stream_id,
                        "segment_id": segment_id,
                        "sample_rate": 16000,
                        "channels": 1,
                        "codec": "pcm16",
                    },
                    session_id=session_id,
                )
            )

            for index, chunk in enumerate(chunks):
                audio.send_binary(build_media_frame(stream_id, segment_id, index, chunk))
                time.sleep(args.chunk_interval_ms / 1000)

            control.send_text(
                build_message(
                    "sensor.audio.segment.finished",
                    "notify",
                    source,
                    target,
                    {
                        "device_id": args.device_id,
                        "stream_id": stream_id,
                        "segment_id": segment_id,
                        "duration_ms": len(chunks) * args.chunk_interval_ms,
                        "bytes": sum(len(chunk) for chunk in chunks),
                        "finish_reason": "endpoint_detected",
                    },
                    session_id=session_id,
                )
            )

            assistant_reply, audio_play = wait_for_reply(control)
            reply_text = str(assistant_reply["payload"].get("text", "")).strip()
            play_stream_id = str(audio_play.get("stream_id") or audio_play["payload"]["stream_id"]).strip()
            print(f"reply_text: {reply_text}")

            with urlopen(
                f"http://{args.host}:{args.port}/stream.wav?{urlencode({'device_id': args.device_id, 'stream_id': play_stream_id})}",
                timeout=args.timeout_seconds,
            ) as response:
                wav_bytes = response.read()

            save_reply_path = build_reply_output_path(args.save_reply, args.wav)
            save_reply_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_reply_path, "wb") as file:
                file.write(wav_bytes)
            print(f"saved_reply_wav: {save_reply_path}")

            control.send_text(
                build_message(
                    "actuator.audio.started",
                    "notify",
                    source,
                    target,
                    {"device_id": args.device_id, "stream_id": play_stream_id},
                    session_id=session_id,
                    stream_id=play_stream_id,
                )
            )
            control.send_text(
                build_message(
                    "actuator.audio.finished",
                    "notify",
                    source,
                    target,
                    {"device_id": args.device_id, "stream_id": play_stream_id},
                    session_id=session_id,
                    stream_id=play_stream_id,
                )
            )
        finally:
            audio.close()
    finally:
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1)
        control.close()


if __name__ == "__main__":
    main()
