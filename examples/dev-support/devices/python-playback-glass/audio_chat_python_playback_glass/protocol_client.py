from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from aiohttp import ClientSession, WSMsgType

PROTOCOL_VERSION = "audio-chat.v1"


def new_id(prefix: str) -> str:
    """生成端侧本地 ID。"""

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_ms() -> int:
    """返回当前毫秒时间戳。"""

    return int(time.time() * 1000)


def ws_url(server_url: str, path: str) -> str:
    """把 HTTP server URL 转成 WebSocket URL。"""

    parsed = urlparse(server_url)
    return urlunparse(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, path, "", "", ""))


def encode_stream_chunk(header: dict[str, Any], payload: bytes) -> bytes:
    """编码 StreamChunk 二进制帧。"""

    header_bytes = json.dumps({**header, "payload_size": len(payload)}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return len(header_bytes).to_bytes(4, "big") + header_bytes + payload


def decode_stream_chunk(raw: bytes) -> dict[str, Any]:
    """解码 StreamChunk 二进制帧。"""

    header_len = int.from_bytes(raw[:4], "big")
    header = json.loads(raw[4 : 4 + header_len].decode("utf-8"))
    payload = raw[4 + header_len :]
    if len(payload) != int(header.get("payload_size") or 0):
        raise ValueError("payload_size mismatch")
    return {**header, "payload": payload}


@dataclass
class PlaybackStats:
    """记录回放端侧看到的协议证据。"""

    registered: bool = False
    received_events: list[dict[str, Any]] = field(default_factory=list)
    sent_events: list[dict[str, Any]] = field(default_factory=list)
    output_chunks: list[dict[str, Any]] = field(default_factory=list)
    input_streams: dict[str, dict[str, Any]] = field(default_factory=dict)
    asset_uploads: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""


class PlaybackProtocolClient:
    """通过真实 Control/Stream WebSocket 伪装成普通眼镜设备。"""

    def __init__(self, *, server_url: str, device: dict[str, Any]) -> None:
        self.server_url = server_url.rstrip("/")
        self.device = device
        self.user_id = str(device["user_id"])
        self.device_id = str(device["device_id"])
        self.stats = PlaybackStats()
        self._control_ws: Any = None
        self._stream_ws: Any = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._output_closed: set[str] = set()

    def event(self, event_name: str, payload: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
        """构造控制事件字典。"""

        return {"event_name": event_name, "timestamp_ms": now_ms(), "user_id": self.user_id, "producer_id": self.device_id, "payload": payload or {}, **extra}

    def registration_payload(self) -> dict[str, Any]:
        """返回设备注册 payload。"""

        keys = ["device_id", "name", "device_name", "client_type", "sdk_version", "auth", "supports", "properties"]
        return {key: self.device[key] for key in keys if key in self.device}

    async def connect(self, session: ClientSession) -> None:
        """连接控制 WebSocket 并完成注册。"""

        self._control_ws = await session.ws_connect(ws_url(self.server_url, "/ws/control"))
        await self.send_event(self.event("control.device.register.requested", self.registration_payload()))
        while True:
            item = await self.receive_control_event(timeout=8)
            if item["event_name"] == "control.device.registered":
                self.stats.registered = True
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                return
            if item["event_name"] == "control.device.register.failed":
                raise RuntimeError(f"device registration failed: {item.get('payload')}")

    async def close(self) -> None:
        """关闭 WebSocket 和心跳任务。"""

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._stream_ws is not None:
            await self._stream_ws.close()
        if self._control_ws is not None:
            await self._control_ws.close()

    async def _heartbeat_loop(self) -> None:
        """定期发送端侧心跳。"""

        while True:
            await asyncio.sleep(10)
            await self.send_event(self.event("control.device.heartbeat.received", {"connection_state": "online", "client_type": self.device.get("client_type")}))

    async def send_event(self, item: dict[str, Any]) -> None:
        """发送控制事件。"""

        self.stats.sent_events.append(item)
        await self._control_ws.send_str(json.dumps(item, ensure_ascii=False))

    async def receive_control_event(self, *, timeout: float) -> dict[str, Any]:
        """读取一个控制事件。"""

        message = await asyncio.wait_for(self._control_ws.receive(), timeout=timeout)
        if message.type != WSMsgType.TEXT:
            raise RuntimeError(f"unexpected control ws message: {message.type}")
        item = json.loads(message.data)
        self.stats.received_events.append(item)
        if item.get("session_id"):
            self.stats.session_id = str(item["session_id"])
        return item

    async def ensure_stream(self, session: ClientSession) -> None:
        """确保 stream WebSocket 已连接。"""

        if self._stream_ws is None or self._stream_ws.closed:
            self._stream_ws = await session.ws_connect(ws_url(self.server_url, f"/ws/stream?device_id={self.device_id}"))

    async def send_stream_chunk(self, *, stream_id: str, stream_type: str, seq: int, payload: bytes, codec: str, sample_rate: int, channels: int, duration_ms: int, final: bool, metadata: dict[str, Any] | None = None) -> None:
        """发送一帧输入 stream chunk。"""

        header = {
            "version": PROTOCOL_VERSION,
            "user_id": self.user_id,
            "session_id": self.device_id,
            "stream_id": stream_id,
            "stream_type": stream_type,
            "seq": seq,
            "timestamp_ms": now_ms(),
            "codec": codec,
            "sample_rate": sample_rate,
            "channels": channels,
            "duration_ms": duration_ms,
            "final": final,
            "metadata": metadata or {},
        }
        await self._stream_ws.send_bytes(encode_stream_chunk(header, payload))

    async def receive_stream_chunk(self, *, timeout: float) -> dict[str, Any]:
        """读取一帧下行 stream chunk。"""

        message = await asyncio.wait_for(self._stream_ws.receive(), timeout=timeout)
        if message.type != WSMsgType.BINARY:
            raise RuntimeError(f"unexpected stream ws message: {message.type}")
        chunk = decode_stream_chunk(message.data)
        if chunk.get("stream_type") == "actuator.speaker":
            self.stats.output_chunks.append({k: v for k, v in chunk.items() if k != "payload"} | {"payload_size": len(chunk["payload"])})
            if chunk["stream_id"] not in self._output_closed:
                await self.send_event(self.event("stream.output.started", {"stream_type": "actuator.speaker"}, session_id=self.device_id, stream_id=chunk["stream_id"], stream_type="actuator.speaker"))
        return chunk

    async def close_output(self, stream_id: str, *, reason: str = "playback_drained") -> None:
        """按协议回执 speaker 输出完成和关闭。"""

        if stream_id in self._output_closed:
            return
        self._output_closed.add(stream_id)
        await self.send_event(self.event("stream.output.finished", {"stream_type": "actuator.speaker"}, session_id=self.device_id, stream_id=stream_id, stream_type="actuator.speaker"))
        await self.send_event(self.event("stream.output.closed", {"stream_type": "actuator.speaker", "reason": reason}, session_id=self.device_id, stream_id=stream_id, stream_type="actuator.speaker"))

    async def send_mic_audio(
        self,
        session: ClientSession,
        *,
        pcm: bytes,
        sample_rate: int,
        chunk_ms: int,
        source_path: str = "",
        tail_silence_ms: int = 800,
    ) -> None:
        """打开 `sensor.mic` 并按实时 chunk 上传 PCM。"""

        await self.ensure_stream(session)
        stream_id = new_id("stream_in")
        chunk_bytes = int(sample_rate * chunk_ms / 1000) * 2
        payload = pcm + b"\x00" * (int(tail_silence_ms / chunk_ms) * chunk_bytes)
        await self.send_event(self.event("stream.input.opened", {"stream_type": "sensor.mic", "format": {"codec": "pcm16le", "sample_rate": sample_rate, "channels": 1, "chunk_ms": chunk_ms}, "source": "python_playback_glass"}, session_id=self.device_id, stream_id=stream_id, stream_type="sensor.mic"))
        self.stats.input_streams[stream_id] = {"stream_type": "sensor.mic", "bytes_sent": 0, "chunks": 0}
        await asyncio.sleep(0.12)
        seq = 0
        for offset in range(0, len(payload), chunk_bytes):
            part = payload[offset : offset + chunk_bytes]
            if len(part) < chunk_bytes:
                part += b"\x00" * (chunk_bytes - len(part))
            await self.send_stream_chunk(
                stream_id=stream_id,
                stream_type="sensor.mic",
                seq=seq,
                payload=part,
                codec="pcm16le",
                sample_rate=sample_rate,
                channels=1,
                duration_ms=chunk_ms,
                final=False,
                metadata={"source_path": source_path} if source_path else None,
            )
            self.stats.input_streams[stream_id]["bytes_sent"] += len(part)
            self.stats.input_streams[stream_id]["chunks"] += 1
            seq += 1
            await asyncio.sleep(chunk_ms / 1000)
        metadata = {"reason": "offline_segment_completed"}
        if source_path:
            metadata["source_path"] = source_path
        await self.send_stream_chunk(stream_id=stream_id, stream_type="sensor.mic", seq=seq, payload=b"", codec="pcm16le", sample_rate=sample_rate, channels=1, duration_ms=0, final=True, metadata=metadata)

    async def send_rgb_fixture(self, session: ClientSession, *, request_event: dict[str, Any], image_path: Path) -> None:
        """按 server 的 `sensor.rgb` 请求上传图片 fixture。"""

        await self.ensure_stream(session)
        stream_id = new_id("stream_rgb")
        request_id = (request_event.get("payload") or {}).get("request_id")
        payload = image_path.read_bytes()
        await self.send_event(self.event("stream.input.opened", {"stream_type": "sensor.rgb", "format": {"codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 1}, "request_id": request_id, "image_count": 1}, session_id=self.device_id, stream_id=stream_id, stream_type="sensor.rgb"))
        await asyncio.sleep(0.12)
        await self.send_stream_chunk(stream_id=stream_id, stream_type="sensor.rgb", seq=0, payload=payload, codec="jpeg", sample_rate=1, channels=1, duration_ms=1, final=True, metadata={"request_id": request_id, "image_index": 0, "image_count": 1, "reason": "fixture_uploaded"})
        await self.send_event(self.event("stream.input.closed", {"stream_type": "sensor.rgb", "reason": "fixture_uploaded", "request_id": request_id, "image_count": 1}, session_id=self.device_id, stream_id=stream_id, stream_type="sensor.rgb"))
        self.stats.asset_uploads.append({"stream_id": stream_id, "stream_type": "sensor.rgb", "path": str(image_path), "payload_size": len(payload)})
