from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

from aiohttp import ClientSession, WSMsgType

from .device import DeviceBuilder
from .diagnostics import DeviceDiagnostics
from .errors import ProtocolError, RegistrationFailedError
from .events import RealtimeAgentEvent
from .stream import StreamChunk, StreamChunkCodec

EventHandler = Callable[[RealtimeAgentEvent], Awaitable[None]]


def ws_url(server_url: str, path: str, query: dict[str, str] | None = None) -> str:
    """把 HTTP server URL 转成 WebSocket URL。

    主要逻辑：保留 host 和端口，将 http/https 分别映射为 ws/wss。
    参数：`server_url` 为 HTTP 服务地址，`path` 为 WebSocket 路径，`query` 为查询参数。
    返回值：WebSocket URL。
    异常情况：URL 缺少 host 时抛出 `ValueError`。
    """

    parsed = urlparse(server_url)
    if not parsed.netloc:
        raise ValueError(f"invalid server_url: {server_url}")
    return urlunparse(
        (
            "wss" if parsed.scheme == "https" else "ws",
            parsed.netloc,
            path,
            "",
            urlencode(query or {}),
            "",
        )
    )


class CommandResponder:
    """命令回执 helper。

    主要功能：封装 `command.accepted/progress/completed/failed`，调用方只关心命令
    ID 和业务 payload。
    """

    def __init__(self, client: "RealtimeAgentDeviceClient", request: RealtimeAgentEvent) -> None:
        self.client = client
        self.request = request
        payload = request.payload or {}
        self.command_id = str(payload.get("command_id") or request.event_id)
        self.command = str(payload.get("command") or "")

    async def accepted(self, payload: dict[str, Any] | None = None) -> None:
        """回报命令已接受。"""

        await self._send("command.accepted", payload or {})

    async def progress(self, payload: dict[str, Any] | None = None) -> None:
        """回报命令执行进度。"""

        await self._send("command.progress", payload or {})

    async def completed(self, payload: dict[str, Any] | None = None) -> None:
        """回报命令完成。"""

        await self._send("command.completed", payload or {})

    async def failed(self, code: str, message: str, *, retryable: bool = False) -> None:
        """回报命令失败。"""

        await self._send("command.failed", {"error": {"code": code, "message": message, "retryable": retryable}})

    async def _send(self, event_name: str, payload: dict[str, Any]) -> None:
        data = {"command_id": self.command_id}
        if self.command:
            data["command"] = self.command
        data.update(payload)
        await self.client.send_event_name(
            event_name,
            data,
            session_id=self.request.session_id,
            stream_id=self.request.stream_id,
            stream_type=self.request.stream_type,
        )


class StreamRequest:
    """stream 打开请求 helper。

    主要功能：在端侧收到 `stream.control.open.requested` 后，封装 opened、write、
    closed、failed 等回执。
    """

    def __init__(self, client: "RealtimeAgentDeviceClient", request: RealtimeAgentEvent) -> None:
        self.client = client
        self.request = request
        self.stream_id = request.stream_id or str((request.payload or {}).get("stream_id") or "")
        self.stream_type = request.stream_type or str((request.payload or {}).get("stream_type") or "")
        if not self.stream_id:
            from .events import new_id

            self.stream_id = new_id("stream")
        self._seq = 0

    async def opened(self, payload: dict[str, Any] | None = None) -> None:
        """发送输入 stream 已打开回执。"""

        await self.client.send_event_name(
            "stream.input.opened",
            {"stream_type": self.stream_type, **(payload or {})},
            session_id=self.client.device_id,
            stream_id=self.stream_id,
            stream_type=self.stream_type,
        )

    async def write(
        self,
        payload: bytes,
        *,
        codec: str,
        sample_rate: int = 1,
        channels: int = 1,
        duration_ms: int = 0,
        final: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """写入一帧 stream chunk。"""

        await self.client.ensure_stream()
        chunk = StreamChunk(
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
        self._seq += 1
        await self.client.send_stream_chunk(chunk)

    async def closed(self, reason: str = "completed") -> None:
        """发送输入 stream 关闭回执。"""

        await self.client.send_event_name(
            "stream.input.closed",
            {"stream_type": self.stream_type, "reason": reason},
            session_id=self.client.device_id,
            stream_id=self.stream_id,
            stream_type=self.stream_type,
        )

    async def failed(self, code: str, message: str) -> None:
        """发送输入 stream 失败回执。"""

        await self.client.send_event_name(
            "stream.input.failed",
            {"stream_type": self.stream_type, "error": {"code": code, "message": message}},
            session_id=self.client.device_id,
            stream_id=self.stream_id,
            stream_type=self.stream_type,
        )


class RealtimeAgentDeviceClient:
    """realtime-agent 端侧通讯客户端。

    主要功能：负责控制 WebSocket、stream WebSocket、注册、事件发送、事件消费、
    心跳和 stream chunk 编解码。
    主要方法：`connect()`、`register()`、`receive_event()`、`send_event()`、
    `ensure_stream()`、`send_stream_chunk()`。
    """

    def __init__(self, *, server_url: str, device: DeviceBuilder | dict[str, Any]) -> None:
        self.server_url = server_url.rstrip("/")
        self.device_builder = device if isinstance(device, DeviceBuilder) else None
        self.device_payload = device.registration_payload() if isinstance(device, DeviceBuilder) else dict(device)
        self.user_id = str((device.user_id if isinstance(device, DeviceBuilder) else self.device_payload.get("user_id")) or "")
        self.device_id = str(self.device_payload["device_id"])
        self.diagnostics = DeviceDiagnostics()
        self.control_ws: Any = None
        self.stream_ws: Any = None
        self.session: ClientSession | None = None
        self.owns_session = False
        self.heartbeat_task: asyncio.Task[None] | None = None
        self.command_handlers: dict[str, Callable[[CommandResponder], Awaitable[None]]] = {}
        self.stream_open_handlers: dict[str, Callable[[StreamRequest], Awaitable[None]]] = {}

    async def connect(self, session: ClientSession | None = None) -> None:
        """连接控制 WebSocket。

        参数：`session` 可复用外部 aiohttp session；不传时 SDK 自建并在 close 时关闭。
        """

        self.session = session or ClientSession()
        self.owns_session = session is None
        self.control_ws = await self.session.ws_connect(ws_url(self.server_url, "/ws/control"))
        self.diagnostics.control_state = "connected"

    async def register(self, *, start_heartbeat: bool = True) -> RealtimeAgentEvent:
        """发送注册事件并等待注册结果。

        参数：`start_heartbeat` 控制注册成功后是否由 SDK 自动发送心跳。长驻端侧通常
        使用默认值；测试或已有自定义心跳循环的参考端可以关闭，避免重复心跳。
        """

        if self.control_ws is None:
            await self.connect()
        await self.send_event_name("control.device.register.requested", self.registration_payload())
        while True:
            event = await self.receive_event(timeout=8)
            if event.event_name == "control.device.registered":
                self.diagnostics.registered = True
                if start_heartbeat:
                    self.heartbeat_task = asyncio.create_task(
                        self._heartbeat_loop(float(event.payload.get("heartbeat_interval_seconds") or 10))
                    )
                return event
            if event.event_name == "control.device.register.failed":
                self.diagnostics.last_error = str(event.payload.get("reason") or event.payload)
                raise RegistrationFailedError(self.diagnostics.last_error)

    def registration_payload(self) -> dict[str, Any]:
        """返回注册 payload。"""

        allowed = {"device_id", "name", "device_name", "client_type", "sdk_version", "auth", "supports", "properties", "runtime"}
        return {key: self.device_payload[key] for key in allowed if key in self.device_payload}

    def event(self, event_name: str, payload: dict[str, Any] | None = None, **extra: Any) -> RealtimeAgentEvent:
        """构造当前设备生产的控制事件。"""

        return RealtimeAgentEvent(
            event_name=event_name,
            user_id=self.user_id,
            producer_id=self.device_id,
            payload=payload or {},
            **extra,
        )

    async def send_event_name(self, event_name: str, payload: dict[str, Any] | None = None, **extra: Any) -> None:
        """按事件名发送控制事件。"""

        await self.send_event(self.event(event_name, payload or {}, **extra))

    async def send_event(self, event: RealtimeAgentEvent) -> None:
        """发送控制事件。"""

        if self.control_ws is None:
            raise ProtocolError("control websocket is not connected")
        self.diagnostics.sent_events += 1
        self.diagnostics.last_event_name = event.event_name
        await self.control_ws.send_str(event.to_json())

    async def receive_event(self, *, timeout: float | None = None) -> RealtimeAgentEvent:
        """读取一个控制事件。"""

        if self.control_ws is None:
            raise ProtocolError("control websocket is not connected")
        receive = self.control_ws.receive()
        message = await asyncio.wait_for(receive, timeout=timeout) if timeout is not None else await receive
        if message.type != WSMsgType.TEXT:
            raise ProtocolError(f"unexpected control ws message: {message.type}")
        event = RealtimeAgentEvent.from_json(message.data)
        self.diagnostics.received_events += 1
        self.diagnostics.last_event_name = event.event_name
        return event

    def on_command(self, command: str, handler: Callable[[CommandResponder], Awaitable[None]]) -> None:
        """注册命令处理回调。"""

        self.command_handlers[command] = handler

    def on_stream_open(self, stream_type: str, handler: Callable[[StreamRequest], Awaitable[None]]) -> None:
        """注册 stream 打开处理回调。"""

        self.stream_open_handlers[stream_type] = handler

    async def dispatch_event(self, event: RealtimeAgentEvent) -> bool:
        """按已注册回调分发事件。

        返回值：命中并执行回调时返回 True，否则返回 False。
        """

        if event.event_name == "command.requested":
            command = str((event.payload or {}).get("command") or "")
            handler = self.command_handlers.get(command)
            if handler is None:
                return False
            await handler(CommandResponder(self, event))
            return True
        if event.event_name == "stream.control.open.requested":
            stream_type = event.stream_type or str((event.payload or {}).get("stream_type") or "")
            handler = self.stream_open_handlers.get(stream_type)
            if handler is None:
                return False
            await handler(StreamRequest(self, event))
            return True
        return False

    async def ensure_stream(self) -> None:
        """确保 stream WebSocket 已连接。"""

        if self.session is None:
            self.session = ClientSession()
            self.owns_session = True
        if self.stream_ws is None or self.stream_ws.closed:
            self.stream_ws = await self.session.ws_connect(ws_url(self.server_url, "/ws/stream", {"device_id": self.device_id}))
            self.diagnostics.stream_state = "connected"

    async def send_stream_chunk(self, chunk: StreamChunk) -> None:
        """发送一帧 stream chunk。"""

        await self.ensure_stream()
        await self.stream_ws.send_bytes(StreamChunkCodec.encode(chunk))

    async def receive_stream_chunk(self, *, timeout: float | None = None) -> StreamChunk:
        """读取一帧下行 stream chunk。"""

        await self.ensure_stream()
        receive = self.stream_ws.receive()
        message = await asyncio.wait_for(receive, timeout=timeout) if timeout is not None else await receive
        if message.type != WSMsgType.BINARY:
            raise ProtocolError(f"unexpected stream ws message: {message.type}")
        chunk = StreamChunkCodec.decode(message.data)
        if chunk.stream_type.startswith("actuator."):
            self.diagnostics.output_chunks += 1
        return chunk

    async def close(self) -> None:
        """关闭心跳任务和 WebSocket。"""

        if self.heartbeat_task is not None:
            self.heartbeat_task.cancel()
        if self.stream_ws is not None:
            await self.stream_ws.close()
        if self.control_ws is not None:
            await self.control_ws.close()
        if self.session is not None and self.owns_session:
            await self.session.close()
        self.diagnostics.control_state = "closed"
        self.diagnostics.stream_state = "closed"

    def diagnostics_snapshot(self) -> dict[str, Any]:
        """返回当前诊断快照。"""

        return self.diagnostics.to_dict()

    async def _heartbeat_loop(self, interval_seconds: float) -> None:
        """按 server 返回的间隔发送端侧心跳。"""

        while True:
            await asyncio.sleep(interval_seconds)
            await self.send_event_name(
                "control.device.heartbeat.received",
                {"connection_state": "online", "client_type": self.device_payload.get("client_type")},
            )
