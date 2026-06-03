from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode, urlparse, urlunparse

from aiohttp import ClientSession, WSMsgType

from realtime_agent.protocol import Event as _ProtocolEvent
from realtime_agent.protocol import StreamChunk, StreamChunkCodec as _ProtocolStreamChunkCodec
from realtime_agent.protocol import new_id


def ws_url(server_url: str, path: str, query: dict[str, Any] | None = None) -> str:
    """把 HTTP server 地址转换成 WebSocket URL。

    主要逻辑：保留 server 的 host、port 和可选 base path，只替换 scheme 并追加
    path/query，供 dev-support 端侧组件连接控制和 stream WebSocket。
    参数：`server_url` 是 HTTP/HTTPS 地址，`path` 是 WebSocket 路径，`query` 是查询参数。
    返回值：`ws://` 或 `wss://` URL。
    异常情况：URL 结构不合法时由 `urlparse` 后续使用方暴露。
    """

    parsed = urlparse(server_url.rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    base_path = parsed.path.rstrip("/")
    target_path = "/" + "/".join(part.strip("/") for part in [base_path, path] if part.strip("/"))
    return urlunparse((scheme, parsed.netloc, target_path, "", urlencode(query or {}), ""))


class RealtimeAgentEvent(_ProtocolEvent):
    """dev-support 端侧使用的控制事件兼容类。

    主要功能：在 server 协议 `Event` 基础上补齐旧 Python 参考端依赖的
    `to_json()`、`from_json()` 和 `from_object()` 方法。
    """

    def to_json(self) -> str:
        """序列化为控制 WebSocket 文本消息。"""

        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> "RealtimeAgentEvent":
        """从控制 WebSocket 文本消息反序列化事件。"""

        return cls.from_object(json.loads(data))

    @classmethod
    def from_object(cls, data: dict[str, Any]) -> "RealtimeAgentEvent":
        """从字典构造事件。"""

        event = _ProtocolEvent.from_dict(data)
        return cls(
            version=event.version,
            event_id=event.event_id,
            event_name=event.event_name,
            timestamp_ms=event.timestamp_ms,
            user_id=event.user_id,
            producer_id=event.producer_id,
            session_id=event.session_id,
            stream_id=event.stream_id,
            stream_type=event.stream_type,
            payload=dict(event.payload or {}),
        )


class StreamChunkCodec:
    """dev-support 端侧使用的 StreamChunk 编解码器兼容类。"""

    @staticmethod
    def encode(chunk: StreamChunk) -> bytes:
        """编码完整 `StreamChunk`。"""

        return _ProtocolStreamChunkCodec.encode(chunk)

    @staticmethod
    def decode(raw: bytes) -> StreamChunk:
        """解码完整 `StreamChunk`。"""

        return _ProtocolStreamChunkCodec.decode(raw)

    @staticmethod
    def encode_header(header: dict[str, Any], payload: bytes) -> bytes:
        """按旧 Python dev-support helper 形态编码 header 和 payload。"""

        chunk = StreamChunk(
            user_id=str(header.get("user_id") or ""),
            session_id=str(header.get("session_id") or ""),
            stream_id=str(header["stream_id"]),
            stream_type=str(header["stream_type"]),
            seq=int(header.get("seq") or 0),
            payload=payload,
            codec=str(header.get("codec") or "pcm16le"),
            sample_rate=int(header.get("sample_rate") or 16000),
            channels=int(header.get("channels") or 1),
            duration_ms=int(header.get("duration_ms") or 20),
            timestamp_ms=int(header.get("timestamp_ms") or 0) or _now_ms(),
            version=str(header.get("version") or "realtime-agent.v1"),
            final=bool(header.get("final", False)),
            metadata=dict(header.get("metadata") or {}),
        )
        return _ProtocolStreamChunkCodec.encode(chunk)

    @staticmethod
    def decode_header(raw: bytes) -> dict[str, Any]:
        """按旧 Python dev-support helper 形态解码为字典。"""

        chunk = _ProtocolStreamChunkCodec.decode(raw)
        return {
            "version": chunk.version,
            "user_id": chunk.user_id,
            "session_id": chunk.session_id,
            "stream_id": chunk.stream_id,
            "stream_type": chunk.stream_type,
            "seq": chunk.seq,
            "timestamp_ms": chunk.timestamp_ms,
            "codec": chunk.codec,
            "sample_rate": chunk.sample_rate,
            "channels": chunk.channels,
            "duration_ms": chunk.duration_ms,
            "payload_size": len(chunk.payload),
            "final": chunk.final,
            "metadata": dict(chunk.metadata or {}),
            "payload": chunk.payload,
        }


class RealtimeAgentDeviceClient:
    """dev-support Python 端侧的最小 WebSocket client。

    主要功能：保留旧 `python-glass` / `python-phone` 参考端需要的注册、事件发送、
    stream chunk 收发和简单事件分发方法。它不是正式 Device SDK，只用于开发支持组件
    在迁移期间复用真实协议。
    """

    def __init__(self, *, server_url: str, device: dict[str, Any]) -> None:
        self.server_url = server_url.rstrip("/")
        self.device = dict(device.registration_payload() if hasattr(device, "registration_payload") else device)
        self.user_id = str(self.device.get("user_id") or "")
        self.device_id = str(self.device.get("device_id") or "")
        self._owned_session: ClientSession | None = None
        self.control_ws: Any = None
        self.stream_ws: Any = None
        self._stream_open_handlers: dict[str, Callable[[Any], Awaitable[None]]] = {}
        self._command_handlers: dict[str, Callable[[Any], Awaitable[None]]] = {}
        self._heartbeat_task: asyncio.Task | None = None

    def event(self, event_name: str, payload: dict[str, Any] | None = None, **extra: Any) -> RealtimeAgentEvent:
        """构造控制事件。"""

        return RealtimeAgentEvent(
            event_name=event_name,
            user_id=self.user_id,
            producer_id=self.device_id,
            payload=payload or {},
            **extra,
        )

    async def connect(self, *, session: ClientSession | None = None) -> None:
        """打开控制 WebSocket。"""

        if session is None:
            self._owned_session = ClientSession()
            session = self._owned_session
        self.control_ws = await session.ws_connect(ws_url(self.server_url, "/ws/control"))

    async def register(self, *, start_heartbeat: bool = True) -> RealtimeAgentEvent:
        """发送设备注册事件并等待注册成功。"""

        payload = {
            key: self.device[key]
            for key in ("device_id", "name", "device_name", "client_type", "sdk_version", "auth", "runtime", "supports", "properties")
            if key in self.device
        }
        await self.send_event(self.event("control.device.register.requested", payload))
        while True:
            event = await self.receive_event(timeout=8)
            if event.event_name == "control.device.registered":
                if start_heartbeat:
                    interval = float((event.payload or {}).get("heartbeat_interval_seconds") or 10)
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(interval))
                return event
            if event.event_name == "control.device.register.failed":
                raise RuntimeError(f"device registration failed: {event.payload}")

    async def send_event(self, event: RealtimeAgentEvent | _ProtocolEvent) -> None:
        """发送控制事件。"""

        if self.control_ws is None:
            raise RuntimeError("control websocket is not connected")
        data = event.to_json() if hasattr(event, "to_json") else json.dumps(event.to_dict(), ensure_ascii=False)
        await self.control_ws.send_str(data)

    async def send_event_name(self, event_name: str, payload: dict[str, Any] | None = None, **extra: Any) -> None:
        """按事件名发送控制事件。"""

        await self.send_event(self.event(event_name, payload or {}, **extra))

    async def receive_event(self, *, timeout: float | None) -> RealtimeAgentEvent:
        """读取一个控制事件。"""

        if self.control_ws is None:
            raise RuntimeError("control websocket is not connected")
        receive = self.control_ws.receive()
        message = await asyncio.wait_for(receive, timeout=timeout) if timeout is not None else await receive
        if message.type != WSMsgType.TEXT:
            raise RuntimeError(f"unexpected control ws message: {message.type}")
        return RealtimeAgentEvent.from_json(message.data)

    async def send_stream_chunk(self, chunk: StreamChunk) -> None:
        """发送二进制 stream chunk。"""

        if self.stream_ws is None:
            raise RuntimeError("stream websocket is not connected")
        await self.stream_ws.send_bytes(StreamChunkCodec.encode(chunk))

    async def ensure_stream(self, *, session: ClientSession | None = None) -> None:
        """确保统一 stream WebSocket 已打开。"""

        if self.stream_ws is not None and not self.stream_ws.closed:
            return
        if session is None:
            if self._owned_session is None:
                self._owned_session = ClientSession()
            session = self._owned_session
        self.stream_ws = await session.ws_connect(ws_url(self.server_url, "/ws/stream", {"device_id": self.device_id}))

    async def receive_stream_chunk(self, *, timeout: float | None) -> StreamChunk:
        """读取二进制 stream chunk。"""

        if self.stream_ws is None:
            raise RuntimeError("stream websocket is not connected")
        receive = self.stream_ws.receive()
        message = await asyncio.wait_for(receive, timeout=timeout) if timeout is not None else await receive
        if message.type != WSMsgType.BINARY:
            raise RuntimeError(f"unexpected stream ws message: {message.type}")
        return StreamChunkCodec.decode(message.data)

    def on_stream_open(self, stream_type: str, handler: Callable[[Any], Awaitable[None]]) -> None:
        """注册 stream open 请求处理器。"""

        self._stream_open_handlers[stream_type] = handler

    def on_command(self, command_name: str, handler: Callable[[Any], Awaitable[None]]) -> None:
        """注册远程命令处理器。"""

        self._command_handlers[command_name] = handler

    async def dispatch_event(self, event: RealtimeAgentEvent) -> bool:
        """按 stream_type 分发 server 控制事件。"""

        if event.event_name == "stream.control.open.requested":
            handler = self._stream_open_handlers.get(str(event.stream_type or ""))
            if handler is None:
                return False
            await handler(_StreamOpenRequest(self, event))
            return True
        if event.event_name == "command.requested":
            command_name = str((event.payload or {}).get("command") or "")
            handler = self._command_handlers.get(command_name)
            if handler is None:
                return False
            await handler(_CommandRequest(self, event))
            return True
        return False

    async def close(self) -> None:
        """关闭控制和 stream WebSocket。"""

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self.stream_ws is not None:
            await self.stream_ws.close()
        if self.control_ws is not None:
            await self.control_ws.close()
        if self._owned_session is not None:
            await self._owned_session.close()

    async def _heartbeat_loop(self, interval_seconds: float) -> None:
        """周期性发送设备心跳。"""

        while True:
            await asyncio.sleep(max(1.0, interval_seconds))
            await self.send_event_name(
                "control.device.heartbeat.received",
                {"device_id": self.device_id, "connection_state": "online", "client_type": self.device.get("client_type")},
                session_id=self.device_id,
            )


def _now_ms() -> int:
    """返回当前毫秒时间戳。"""

    import time

    return int(time.time() * 1000)


class DeviceBuilder:
    """构建设备注册 payload 的轻量 builder。

    主要功能：保留历史 Python Device SDK 的链式配置形态，供现有 dev-support
    和 interop 测试继续生成结构化 supports/properties。
    """

    def __init__(self, device_id: str) -> None:
        self._payload: dict[str, Any] = {
            "device_id": device_id,
            "client_type": "python-dev-support",
            "sdk_version": "realtime-agent-device-dev-support-0.1.0",
            "auth": {"mode": "disabled"},
            "supports": {"sensors": [], "actuators": []},
            "properties": {},
        }

    @classmethod
    def define(cls, device_id: str) -> "DeviceBuilder":
        """创建 builder。"""

        return cls(device_id)

    def user(self, user_id: str) -> "DeviceBuilder":
        """设置 user_id。"""

        self._payload["user_id"] = user_id
        return self

    def name(self, name: str) -> "DeviceBuilder":
        """设置设备名称。"""

        self._payload["name"] = name
        self._payload["device_name"] = name
        return self

    def sensor_rgb(self, *, modes: list[str] | None = None, format: str = "jpeg", frequency_hz: float = 1, **extra: Any) -> "DeviceBuilder":
        """声明 RGB 传感器能力。"""

        self._payload["supports"].setdefault("sensors", []).append(
            {
                "type": "rgb",
                "modes": modes or ["single"],
                "default": {"format": format, "frequency_hz": frequency_hz, **extra},
            }
        )
        return self

    def property(self, key: str, value: Any) -> "DeviceBuilder":
        """设置设备 property。"""

        self._payload.setdefault("properties", {})[key] = value
        return self

    def registration_payload(self) -> dict[str, Any]:
        """返回注册 payload。"""

        return dict(self._payload)


class _StreamOpenRequest:
    """传递给 stream open handler 的 helper。"""

    def __init__(self, client: RealtimeAgentDeviceClient, request: RealtimeAgentEvent) -> None:
        self.client = client
        self.request = request
        self.stream_id = request.stream_id or new_id("stream_in")
        self.stream_type = str(request.stream_type or "")
        self._seq = 0

    async def opened(self, payload: dict[str, Any] | None = None) -> None:
        """发送 stream.input.opened。"""

        await self.client.send_event_name(
            "stream.input.opened",
            {"stream_type": self.stream_type, **dict(payload or {})},
            session_id=self.client.device_id,
            stream_id=self.stream_id,
            stream_type=self.stream_type,
        )

    async def write(
        self,
        payload: bytes,
        *,
        codec: str,
        sample_rate: int,
        channels: int,
        duration_ms: int = 1,
        final: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """写入一个输入 stream chunk。"""

        await self.client.ensure_stream()
        await self.client.send_stream_chunk(
            StreamChunk(
                user_id=self.client.user_id,
                session_id=self.client.device_id,
                stream_id=self.stream_id,
                stream_type=self.stream_type,
                seq=self._seq,
                payload=payload,
                codec=codec,
                sample_rate=sample_rate,
                channels=channels,
                duration_ms=duration_ms,
                final=final,
                metadata=metadata or {},
            )
        )
        self._seq += 1

    async def closed(self, payload: dict[str, Any] | None = None) -> None:
        """发送 stream.input.closed。"""

        await self.client.send_event_name(
            "stream.input.closed",
            {"stream_type": self.stream_type, "reason": "dev_support_closed", **dict(payload or {})},
            session_id=self.client.device_id,
            stream_id=self.stream_id,
            stream_type=self.stream_type,
        )


class _CommandRequest:
    """传递给 command handler 的 helper。"""

    def __init__(self, client: RealtimeAgentDeviceClient, request: RealtimeAgentEvent) -> None:
        self.client = client
        self.request = request
        self.payload = dict(request.payload or {})
        self.command_id = str(self.payload.get("command_id") or "")
        self.command = str(self.payload.get("command") or "")

    async def accepted(self, payload: dict[str, Any] | None = None) -> None:
        """发送 command.accepted。"""

        await self._emit("command.accepted", payload or {})

    async def progress(self, payload: dict[str, Any] | None = None) -> None:
        """发送 command.progress。"""

        await self._emit("command.progress", payload or {})

    async def completed(self, payload: dict[str, Any] | None = None) -> None:
        """发送 command.completed。"""

        await self._emit("command.completed", payload or {})

    async def failed(self, payload: dict[str, Any] | None = None) -> None:
        """发送 command.failed。"""

        await self._emit("command.failed", payload or {})

    async def _emit(self, event_name: str, payload: dict[str, Any]) -> None:
        data = {"command_id": self.command_id, "command": self.command, **payload}
        await self.client.send_event_name(event_name, data, session_id=self.client.device_id)
