from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from dataclasses import dataclass, field
from typing import Any

from aiohttp import WSMsgType, web

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunkCodec


@dataclass
class NetworkDeviceConnection:
    """真实 endpoint 的网络连接状态。

    主要功能：把同步 Control / Stream Service 的下行投递转换为 aiohttp WebSocket
    队列写入。
    主要属性：`device_id` 标识端侧，`event_queue` 存放下行控制事件，`stream_queue`
    存放下行二进制 stream chunk。
    """

    device_id: str
    loop: asyncio.AbstractEventLoop
    event_queue: asyncio.Queue[Event] = field(default_factory=asyncio.Queue)
    stream_queue: asyncio.Queue[bytes] = field(default_factory=asyncio.Queue)
    connection_id: str | None = None
    _control_ws: web.WebSocketResponse | None = None
    _stream_ws: web.WebSocketResponse | None = None

    def bind_control_ws(self, ws: web.WebSocketResponse) -> None:
        """绑定控制 WebSocket。

        主要逻辑：保存当前 WebSocket，供重连覆盖和关闭旧连接使用。
        参数：`ws` 为 aiohttp WebSocket。
        返回值：无。
        异常情况：无。
        """
        self._control_ws = ws

    def bind_stream_ws(self, ws: web.WebSocketResponse) -> None:
        """绑定 stream WebSocket。

        主要逻辑：stream 连接可以晚于控制连接建立，绑定后 sender task 会消费缓存队列。
        参数：`ws` 为 aiohttp WebSocket。
        返回值：无。
        异常情况：无。
        """
        self._stream_ws = ws

    def push_event(self, event: Event) -> None:
        """投递下行控制事件。

        主要逻辑：服务层是同步 API，因此通过 event loop 安全地写入异步队列。
        参数：`event` 为要下发给端侧的事件。
        返回值：无。
        异常情况：队列写入失败时异常会留在 loop 回调中。
        """
        self.loop.call_soon_threadsafe(self.event_queue.put_nowait, event)

    def push_stream_chunk(self, chunk: object) -> None:
        """投递下行 stream chunk。

        主要逻辑：把 `StreamChunk` 编码成协议二进制后写入 stream 队列。
        参数：`chunk` 为 `StreamChunk` 对象。
        返回值：无。
        异常情况：对象不符合编码协议时会在调用方线程抛出异常。
        """
        self.loop.call_soon_threadsafe(self.stream_queue.put_nowait, StreamChunkCodec.encode(chunk))  # type: ignore[arg-type]

    def close(self, *, reason: str) -> None:
        """关闭当前网络连接。

        主要逻辑：设备同 `device_id` 重连时由 Control Service 调用，关闭旧控制和 stream
        WebSocket，避免同一设备多连接同时收下行数据。
        参数：`reason` 为关闭原因。
        返回值：无。
        异常情况：WebSocket 已关闭时忽略。
        """
        for ws in (self._control_ws, self._stream_ws):
            if ws is not None and not ws.closed:
                self.loop.call_soon_threadsafe(asyncio.create_task, ws.close(message=reason.encode("utf-8")))


class AudioChatHttpServer:
    """audio-chat HTTP/WebSocket server。

    主要功能：暴露健康检查、debug API、控制 WebSocket 和 stream WebSocket，
    让真实端侧通过协议连接 `AudioChatApp`。
    主要方法：`create_web_app()` 构建 aiohttp app，`control_ws()` 和 `stream_ws()`
    分别处理控制与二进制流。
    """

    def __init__(self, audio_app: AudioChatApp) -> None:
        self.audio_app = audio_app
        self.connections: dict[str, NetworkDeviceConnection] = {}

    def create_web_app(self) -> web.Application:
        """创建 aiohttp 应用。

        主要逻辑：注册 HTTP debug 路由和两条 WebSocket 路由。
        参数：无。
        返回值：`web.Application`。
        异常情况：无。
        """
        app = web.Application()
        app["audio_chat_server"] = self
        app.router.add_get("/api/health", self.health)
        app.router.add_get("/api/debug/devices", self.debug_devices)
        app.router.add_get("/api/debug/devices/{device_id}", self.debug_device)
        app.router.add_get("/api/debug/users/{user_id}", self.debug_user)
        app.router.add_get("/ws/control", self.control_ws)
        app.router.add_get("/ws/stream", self.stream_ws)
        return app

    async def health(self, _request: web.Request) -> web.Response:
        """返回服务健康状态。

        主要逻辑：用于 preflight 和本地联调确认 server 已启动。
        参数：aiohttp request。
        返回值：JSON response。
        异常情况：无。
        """
        return web.json_response({"status": "ok", "protocol_version": "audio-chat.v1"})

    async def debug_devices(self, _request: web.Request) -> web.Response:
        """返回设备连接快照。

        主要逻辑：读取 Control Service 的 debug snapshot，包含 connection_id、last_seen_at、
        connection_state、capabilities 和 subscriptions。
        参数：aiohttp request。
        返回值：JSON response。
        异常情况：无。
        """
        return web.json_response(self.audio_app.control_service.build_devices_snapshot())

    async def debug_device(self, request: web.Request) -> web.Response:
        """返回单个设备的 debug 快照。"""

        snapshot = self.audio_app.control_service.build_device_snapshot(request.match_info["device_id"])
        if snapshot is None:
            raise web.HTTPNotFound(text="device not found")
        return web.json_response(snapshot)

    async def debug_user(self, request: web.Request) -> web.Response:
        """返回指定用户的设备快照。

        主要逻辑：按路径参数 `user_id` 查询用户设备状态。
        参数：aiohttp request。
        返回值：JSON response。
        异常情况：无。
        """
        return web.json_response(self.audio_app.control_service.build_user_snapshot(request.match_info["user_id"]))

    async def control_ws(self, request: web.Request) -> web.WebSocketResponse:
        """处理控制 WebSocket。

        主要逻辑：接收端侧 Event JSON；注册事件会绑定连接，其他事件进入
        `AudioChatApp.publish_control_event()`。下行事件由 sender task 从队列发送。
        参数：aiohttp request。
        返回值：WebSocket response。
        异常情况：JSON 或协议错误会向端侧发送 `system.error.raised` 后继续等待下一条。
        """
        ws = web.WebSocketResponse(heartbeat=15)
        await ws.prepare(request)
        loop = asyncio.get_running_loop()
        connection: NetworkDeviceConnection | None = None
        sender_task: asyncio.Task | None = None

        async def sender(target: NetworkDeviceConnection) -> None:
            while not ws.closed:
                event = await target.event_queue.get()
                await ws.send_str(json.dumps(event.to_dict(), ensure_ascii=False))

        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                event: Event | None = None
                try:
                    event = Event.from_dict(json.loads(message.data))
                    if event.event_name == "control.device.register.requested":
                        device_id = str(event.payload.get("device_id") or event.producer_id)
                        connection = NetworkDeviceConnection(device_id=device_id, loop=loop)
                        connection.bind_control_ws(ws)
                        registered = self.audio_app.register_device(event, connection)
                        connection.connection_id = registered.payload.get("connection_id")
                        self.connections[device_id] = connection
                        await ws.send_str(json.dumps(registered.to_dict(), ensure_ascii=False))
                        sender_task = asyncio.create_task(sender(connection))
                    else:
                        self.audio_app.publish_control_event(event)
                except Exception as exc:
                    error = self._error_event(exc, event=event, raw=message.data)
                    self.audio_app.recorder.record_event(error)
                    self.audio_app.recorder.record_system_event(error.to_dict())
                    await ws.send_str(json.dumps(error.to_dict(), ensure_ascii=False))
        finally:
            if sender_task is not None:
                sender_task.cancel()
            if connection is not None:
                self.audio_app.control_service.mark_connection_offline(
                    connection.device_id,
                    connection_id=connection.connection_id,
                    reason="control_ws_disconnected",
                )
        return ws

    async def stream_ws(self, request: web.Request) -> web.WebSocketResponse:
        """处理二进制 stream WebSocket。

        主要逻辑：上行消息用 `StreamChunkCodec.decode()` 解码后写入 Stream Service；
        下行消息从设备连接的 stream 队列读取并按原始二进制发送。
        参数：aiohttp request，必须带 `device_id` query。
        返回值：WebSocket response。
        异常情况：未注册设备连接会返回 404。
        """
        device_id = request.query.get("device_id", "")
        connection = self.connections.get(device_id)
        if connection is None:
            raise web.HTTPNotFound(text="device is not registered on control websocket")
        ws = web.WebSocketResponse(heartbeat=15)
        await ws.prepare(request)
        connection.bind_stream_ws(ws)
        reported_errors: set[str] = set()

        async def sender() -> None:
            while not ws.closed:
                raw = await connection.stream_queue.get()
                await ws.send_bytes(raw)

        sender_task = asyncio.create_task(sender())
        try:
            async for message in ws:
                if message.type != WSMsgType.BINARY:
                    continue
                try:
                    chunk = StreamChunkCodec.decode(message.data)
                    self.audio_app.write_input_chunk(chunk)
                except Exception as exc:
                    chunk_info = locals().get("chunk")
                    if chunk_info is not None:
                        user_id = getattr(chunk_info, "user_id", "unknown")
                        session_id = getattr(chunk_info, "session_id", None)
                        stream_id = getattr(chunk_info, "stream_id", None)
                        stream_type = getattr(chunk_info, "stream_type", None)
                    else:
                        user_id = "unknown"
                        session_id = None
                        stream_id = None
                        stream_type = None
                    dedupe_key = f"{device_id}:{stream_id}:{type(exc).__name__}:{str(exc)}"
                    error = Event(
                        event_name="system.error.raised",
                        user_id=user_id,
                        producer_id="server-main",
                        session_id=session_id,
                        stream_id=stream_id,
                        stream_type=stream_type,
                        payload={
                            "message": str(exc),
                            "transport": "stream_ws",
                            "device_id": device_id,
                        },
                    )
                    self.audio_app.recorder.record_event(error)
                    self.audio_app.recorder.record_system_event(error.to_dict())
                    if dedupe_key not in reported_errors:
                        reported_errors.add(dedupe_key)
                        await ws.send_str(json.dumps(error.to_dict(), ensure_ascii=False))
        finally:
            sender_task.cancel()
        return ws

    @staticmethod
    def _error_event(exc: Exception, *, event: Event | None, raw: str) -> Event:
        user_id = event.user_id if event is not None else "unknown"
        producer_id = event.producer_id if event is not None else "unknown"
        return Event(
            event_name="system.error.raised",
            user_id=user_id,
            producer_id="server-main",
            payload={
                "message": str(exc),
                "source_producer_id": producer_id,
                "raw_event": raw[:512],
            },
        )


def _load_app_module(path: str, audio_app: AudioChatApp) -> None:
    """加载业务扩展模块。

    主要逻辑：支持 `module:factory`，优先调用 `factory(audio_app)`，如果工厂不接收参数
    则调用 `factory()`。本阶段只预留注册入口，不实现业务迁移。
    参数：`path` 为命令行传入的模块路径，`audio_app` 为 SDK app。
    返回值：无。
    异常情况：模块导入或工厂执行失败时向上抛出。
    """
    if not path:
        return
    module_name, _, factory_name = path.partition(":")
    module = importlib.import_module(module_name)
    if not factory_name:
        return
    factory = getattr(module, factory_name)
    try:
        factory(audio_app)
    except TypeError:
        factory()


def main(argv: list[str] | None = None) -> None:
    """启动 audio-chat server。

    主要逻辑：读取 YAML 配置，创建 `AudioChatApp`，加载可选业务扩展模块，然后启动
    aiohttp HTTP/WebSocket 服务。
    参数：`argv` 为可选命令行参数。
    返回值：无。
    异常情况：配置文件缺失、端口占用或扩展模块错误时进程退出并显示异常。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="audio-chat/examples/minimal/server.yaml")
    parser.add_argument("--app-module", default="")
    args = parser.parse_args(argv)

    config = AudioChatConfig.from_yaml(args.config)
    audio_app = AudioChatApp(config)
    _load_app_module(args.app_module, audio_app)
    server = AudioChatHttpServer(audio_app)
    web.run_app(server.create_web_app(), host=config.server_host, port=config.server_port)
