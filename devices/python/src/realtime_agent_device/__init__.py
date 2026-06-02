from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from aiohttp import ClientSession, WSMsgType

from realtime_agent.protocol import Event as RealtimeAgentEvent
from realtime_agent.protocol import StreamChunk, StreamChunkCodec as ServerStreamChunkCodec
from realtime_agent.protocol import new_id


def _event_to_json(self: RealtimeAgentEvent) -> str:
    """将控制事件编码为 JSON 字符串。

    主要逻辑：复用 server SDK 的 `to_dict()` 校验逻辑，确保 Python Device SDK
    与服务端控制事件信封完全一致。
    参数：无。
    返回值：可直接写入控制 WebSocket 的 JSON 字符串。
    异常情况：事件字段不符合协议时由 `to_dict()` 抛出 ValueError。
    """

    return json.dumps(self.to_dict(), ensure_ascii=False)


def _event_from_json(cls: type[RealtimeAgentEvent], raw: str | bytes) -> RealtimeAgentEvent:
    """从 JSON 字符串解码控制事件。

    主要逻辑：读取 JSON 后交给 server SDK 的 `from_dict()`，避免端侧维护第二套
    协议校验。
    参数：`raw` 为 WebSocket 收到的文本帧。
    返回值：`RealtimeAgentEvent`。
    异常情况：JSON 非法或事件字段不合法时抛出对应异常。
    """

    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    return cls.from_dict(json.loads(text))


if not hasattr(RealtimeAgentEvent, "to_json"):
    setattr(RealtimeAgentEvent, "to_json", _event_to_json)
if not hasattr(RealtimeAgentEvent, "from_json"):
    setattr(RealtimeAgentEvent, "from_json", classmethod(_event_from_json))


Event = RealtimeAgentEvent


class StreamChunkCodec:
    """端侧 StreamChunk 编解码器。

    主要功能：对外暴露 Python Device SDK 使用的 stream 编解码 API，同时内部委托给
    server SDK 协议实现。
    主要方法：`encode()`、`decode()`、`encode_header()`、`decode_header()`。
    """

    @staticmethod
    def encode(chunk: StreamChunk) -> bytes:
        """编码完整 `StreamChunk`。

        参数：`chunk` 为待发送的 stream 数据片。
        返回值：协议二进制帧。
        异常情况：chunk 字段不完整时由 server codec 抛出异常。
        """

        return ServerStreamChunkCodec.encode(chunk)

    @staticmethod
    def decode(raw: bytes) -> StreamChunk:
        """解码完整 `StreamChunk`。

        参数：`raw` 为 WebSocket 二进制帧。
        返回值：`StreamChunk`。
        异常情况：header 长度、JSON 或 payload_size 不合法时抛出 ValueError。
        """

        return ServerStreamChunkCodec.decode(raw)

    @staticmethod
    def encode_header(header: dict[str, Any], payload: bytes) -> bytes:
        """按 header 字典和 payload 编码 stream 帧。

        主要逻辑：这是回放参考端的兼容入口；先把 header 规范化为 `StreamChunk`，
        再复用正式 codec。
        参数：`header` 为协议头字段，`payload` 为二进制载荷。
        返回值：协议二进制帧。
        异常情况：缺少必要 header 字段时抛出 KeyError 或 ValueError。
        """

        chunk = StreamChunk(
            version=str(header.get("version") or "realtime-agent.v1"),
            user_id=str(header.get("user_id") or "user-device"),
            session_id=str(header.get("session_id") or "session-device"),
            stream_id=str(header["stream_id"]),
            stream_type=str(header["stream_type"]),
            seq=int(header.get("seq", 0)),
            timestamp_ms=int(header.get("timestamp_ms", 0) or 0),
            codec=str(header.get("codec") or "pcm16le"),
            sample_rate=int(header.get("sample_rate", 16000)),
            channels=int(header.get("channels", 1)),
            duration_ms=int(header.get("duration_ms", 20)),
            final=bool(header.get("final", False)),
            metadata=dict(header.get("metadata") or {}),
            payload=payload,
        )
        return ServerStreamChunkCodec.encode(chunk)

    @staticmethod
    def decode_header(raw: bytes) -> dict[str, Any]:
        """解码 stream 帧并返回包含 payload 的字典。

        参数：`raw` 为 WebSocket 二进制帧。
        返回值：包含 header 字段和 `payload` 的字典。
        异常情况：帧非法时由正式 codec 抛出 ValueError。
        """

        chunk = ServerStreamChunkCodec.decode(raw)
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
            "metadata": dict(chunk.metadata),
            "payload": chunk.payload,
        }


def ws_url(server_url: str, path: str, query: dict[str, Any] | None = None) -> str:
    """把 HTTP server URL 转为 WebSocket URL。

    参数：`server_url` 为 `http://` 或 `https://` 地址，`path` 为 WebSocket 路径，
    `query` 为可选查询参数。
    返回值：完整 WebSocket URL。
    异常情况：无；未知 scheme 按普通字符串替换处理。
    """

    base = server_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    suffix = path if path.startswith("/") else f"/{path}"
    if query:
        suffix = f"{suffix}?{urlencode({key: str(value) for key, value in query.items()})}"
    return f"{base}{suffix}"


@dataclass
class DeviceBuilder:
    """端侧能力声明构造器。

    主要功能：为测试和参考端提供链式 API 构造设备注册 payload。
    主要属性：`device_id`、`payload`。
    主要方法：`define()`、`user()`、`name()`、`sensor_rgb()`、`property()`。
    """

    device_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def define(cls, device_id: str) -> "DeviceBuilder":
        """创建设备声明构造器。

        参数：`device_id` 为端侧设备 ID。
        返回值：新的 `DeviceBuilder`。
        异常情况：无。
        """

        return cls(
            device_id=device_id,
            payload={
                "device_id": device_id,
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [], "actuators": []},
                "properties": {},
            },
        )

    def user(self, user_id: str) -> "DeviceBuilder":
        """设置默认用户 ID。"""

        self.payload["user_id"] = user_id
        return self

    def name(self, name: str) -> "DeviceBuilder":
        """设置设备名称。"""

        self.payload["name"] = name
        self.payload["device_name"] = name
        return self

    def sensor_rgb(self, *, modes: list[str] | None = None, format: str = "jpeg", frequency_hz: int = 1) -> "DeviceBuilder":
        """声明 RGB 传感器能力。

        参数：`modes` 为支持模式，`format` 为默认图片格式，`frequency_hz` 为采样频率。
        返回值：当前 builder，便于链式调用。
        异常情况：无。
        """

        supports = self.payload.setdefault("supports", {"sensors": [], "actuators": []})
        sensors = supports.setdefault("sensors", [])
        sensors.append(
            {
                "type": "rgb",
                "modes": list(modes or ["single"]),
                "default": {"format": format, "frequency_hz": frequency_hz},
            }
        )
        return self

    def property(self, key: str, value: Any) -> "DeviceBuilder":
        """设置设备属性。"""

        self.payload.setdefault("properties", {})[key] = value
        return self

    def to_dict(self) -> dict[str, Any]:
        """返回注册 payload 副本。"""

        return dict(self.payload)


@dataclass(frozen=True)
class DeviceStreamRequest:
    """服务端请求端侧打开输入 stream 的包装对象。

    主要功能：给端侧 handler 提供 `opened()`、`write()`、`closed()` 便捷方法。
    """

    client: "RealtimeAgentDeviceClient"
    request: RealtimeAgentEvent

    async def opened(self, payload: dict[str, Any] | None = None) -> None:
        """发送 `stream.input.opened`。"""

        await self.client.send_event_name(
            "stream.input.opened",
            {"stream_type": self.request.stream_type, **dict(payload or {})},
            session_id=self.request.session_id or self.client.device_id,
            stream_id=self.request.stream_id or new_id("stream_in"),
            stream_type=self.request.stream_type,
        )

    async def write(
        self,
        payload: bytes,
        *,
        codec: str,
        sample_rate: int,
        channels: int,
        final: bool = False,
        duration_ms: int = 20,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """写入一帧输入 stream chunk。"""

        await self.client.send_stream_chunk(
            StreamChunk(
                user_id=self.client.user_id,
                session_id=self.request.session_id or self.client.device_id,
                stream_id=self.request.stream_id or new_id("stream_in"),
                stream_type=str(self.request.stream_type or ""),
                seq=self.client.next_seq(self.request.stream_id or "stream_in"),
                payload=payload,
                codec=codec,
                sample_rate=sample_rate,
                channels=channels,
                duration_ms=duration_ms,
                final=final,
                metadata=dict(metadata or {}),
            )
        )

    async def closed(self, payload: dict[str, Any] | None = None) -> None:
        """发送 `stream.input.closed`。"""

        await self.client.send_event_name(
            "stream.input.closed",
            {"stream_type": self.request.stream_type, "reason": "device_stream_closed", **dict(payload or {})},
            session_id=self.request.session_id or self.client.device_id,
            stream_id=self.request.stream_id,
            stream_type=self.request.stream_type,
        )


@dataclass(frozen=True)
class DeviceCommandRequest:
    """服务端命令请求包装对象。

    主要功能：给端侧命令 handler 提供 accepted、progress、completed、failed 回执方法。
    """

    client: "RealtimeAgentDeviceClient"
    request: RealtimeAgentEvent

    async def accepted(self, payload: dict[str, Any] | None = None) -> None:
        """发送 `command.accepted`。"""

        await self._send("command.accepted", payload)

    async def progress(self, payload: dict[str, Any] | None = None) -> None:
        """发送 `command.progress`。"""

        await self._send("command.progress", payload)

    async def completed(self, payload: dict[str, Any] | None = None) -> None:
        """发送 `command.completed`。"""

        await self._send("command.completed", payload)

    async def failed(self, payload: dict[str, Any] | None = None) -> None:
        """发送 `command.failed`。"""

        await self._send("command.failed", payload)

    async def _send(self, event_name: str, payload: dict[str, Any] | None) -> None:
        body = dict(payload or {})
        if "command_id" not in body and self.request.payload.get("command_id"):
            body["command_id"] = self.request.payload["command_id"]
        await self.client.send_event_name(
            event_name,
            body,
            session_id=self.request.session_id or self.client.device_id,
        )


class RealtimeAgentDeviceClient:
    """Python Device SDK WebSocket 客户端。

    主要功能：连接 server 的控制和 stream WebSocket，完成设备注册、事件收发、
    stream chunk 收发，以及把服务端请求分发给本地 handler。
    主要方法：`connect()`、`register()`、`receive_event()`、`dispatch_event()`。
    主要属性：`server_url`、`device`、`control_ws`、`stream_ws`。
    """

    def __init__(self, *, server_url: str, device: dict[str, Any] | DeviceBuilder) -> None:
        self.server_url = server_url.rstrip("/")
        self.device = device.to_dict() if isinstance(device, DeviceBuilder) else dict(device)
        self.user_id = str(self.device.get("user_id") or "default")
        self.device_id = str(self.device["device_id"])
        self.control_ws: Any = None
        self.stream_ws: Any = None
        self._session: ClientSession | None = None
        self._owns_session = False
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._stream_handlers: dict[str, Callable[[DeviceStreamRequest], Awaitable[None]]] = {}
        self._command_handlers: dict[str, Callable[[DeviceCommandRequest], Awaitable[None]]] = {}
        self._seq_by_stream: dict[str, int] = {}

    def on_stream_open(self, stream_type: str, handler: Callable[[DeviceStreamRequest], Awaitable[None]]) -> None:
        """注册输入 stream 打开请求 handler。"""

        self._stream_handlers[stream_type] = handler

    def on_command(self, name: str, handler: Callable[[DeviceCommandRequest], Awaitable[None]]) -> None:
        """注册设备命令 handler。"""

        self._command_handlers[name] = handler

    async def connect(self, *, session: ClientSession | None = None) -> None:
        """连接控制 WebSocket。

        参数：`session` 可传入外部 aiohttp session；为空时客户端自行创建。
        返回值：无。
        异常情况：连接失败时由 aiohttp 抛出异常。
        """

        if session is None:
            session = ClientSession()
            self._owns_session = True
        self._session = session
        self.control_ws = await session.ws_connect(ws_url(self.server_url, "/ws/control"))

    async def ensure_stream(self) -> None:
        """确保兼容 `/ws/stream` WebSocket 已连接。"""

        if self._session is None:
            raise RuntimeError("client is not connected")
        if self.stream_ws is None or self.stream_ws.closed:
            self.stream_ws = await self._session.ws_connect(ws_url(self.server_url, "/ws/stream", {"device_id": self.device_id}))

    async def register(self, *, start_heartbeat: bool = True) -> RealtimeAgentEvent:
        """发送设备注册事件并等待注册成功。

        参数：`start_heartbeat` 为真时注册后启动后台心跳。
        返回值：server 返回的 `control.device.registered` 事件。
        异常情况：注册失败或收到异常事件时抛出 RuntimeError。
        """

        payload = {key: value for key, value in self.device.items() if key != "user_id"}
        await self.send_event_name("control.device.register.requested", payload)
        while True:
            event = await self.receive_event(timeout=8)
            if event.event_name == "control.device.registered":
                if start_heartbeat:
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                return event
            if event.event_name == "control.device.register.failed":
                raise RuntimeError(f"device registration failed: {event.payload}")

    async def receive_event(self, *, timeout: float | None = None) -> RealtimeAgentEvent:
        """读取一个控制事件。"""

        if self.control_ws is None:
            raise RuntimeError("control websocket is not connected")
        receive = self.control_ws.receive()
        message = await asyncio.wait_for(receive, timeout=timeout) if timeout is not None else await receive
        if message.type != WSMsgType.TEXT:
            raise RuntimeError(f"unexpected control websocket message: {message.type}")
        return RealtimeAgentEvent.from_json(message.data)  # type: ignore[attr-defined]

    async def send_event(self, event: RealtimeAgentEvent) -> None:
        """发送控制事件。"""

        if self.control_ws is None:
            raise RuntimeError("control websocket is not connected")
        await self.control_ws.send_str(event.to_json())  # type: ignore[attr-defined]

    async def send_event_name(
        self,
        event_name: str,
        payload: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        stream_id: str | None = None,
        stream_type: str | None = None,
    ) -> None:
        """按事件名发送控制事件。"""

        await self.send_event(
            RealtimeAgentEvent(
                event_name=event_name,
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=session_id,
                stream_id=stream_id,
                stream_type=stream_type,
                payload=dict(payload or {}),
            )
        )

    async def send_stream_chunk(self, chunk: StreamChunk) -> None:
        """发送一帧 stream chunk。"""

        await self.ensure_stream()
        await self.stream_ws.send_bytes(StreamChunkCodec.encode(chunk))

    async def receive_stream_chunk(self, *, timeout: float | None = None) -> StreamChunk:
        """接收一帧 stream chunk。"""

        await self.ensure_stream()
        receive = self.stream_ws.receive()
        message = await asyncio.wait_for(receive, timeout=timeout) if timeout is not None else await receive
        if message.type != WSMsgType.BINARY:
            raise RuntimeError(f"unexpected stream websocket message: {message.type}")
        return StreamChunkCodec.decode(message.data)

    async def dispatch_event(self, event: RealtimeAgentEvent) -> bool:
        """把服务端控制事件分发给注册的本地 handler。

        参数：`event` 为刚收到的控制事件。
        返回值：如果事件被本地 handler 消费则返回 True，否则返回 False。
        异常情况：handler 内部异常会向上传播，便于测试暴露真实问题。
        """

        if event.event_name == "stream.control.open.requested":
            handler = self._stream_handlers.get(str(event.stream_type or ""))
            if handler is None:
                return False
            await handler(DeviceStreamRequest(client=self, request=event))
            return True
        if event.event_name == "command.requested":
            name = str(event.payload.get("name") or event.payload.get("command") or "")
            handler = self._command_handlers.get(name)
            if handler is None:
                return False
            await handler(DeviceCommandRequest(client=self, request=event))
            return True
        return False

    def next_seq(self, stream_id: str) -> int:
        """返回并递增指定 stream 的 seq。"""

        value = self._seq_by_stream.get(stream_id, 0)
        self._seq_by_stream[stream_id] = value + 1
        return value

    async def close(self) -> None:
        """关闭 WebSocket、心跳和内部 session。"""

        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
        if self.stream_ws is not None:
            await self.stream_ws.close()
        if self.control_ws is not None:
            await self.control_ws.close()
        if self._session is not None and self._owns_session:
            await self._session.close()

    async def _heartbeat_loop(self) -> None:
        """定期发送心跳。"""

        while True:
            await asyncio.sleep(10)
            await self.send_event_name(
                "control.device.heartbeat.received",
                {"device_id": self.device_id, "connection_state": "online", "client_type": self.device.get("client_type")},
            )


__all__ = [
    "DeviceBuilder",
    "DeviceCommandRequest",
    "DeviceStreamRequest",
    "Event",
    "RealtimeAgentDeviceClient",
    "RealtimeAgentEvent",
    "StreamChunk",
    "StreamChunkCodec",
    "new_id",
    "ws_url",
]
