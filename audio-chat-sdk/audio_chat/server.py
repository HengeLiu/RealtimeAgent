from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from dataclasses import dataclass, field
from typing import Any

from aiohttp import WSMsgType, web

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.app_loader import load_app_config, load_config_as_app
from audio_chat.observability import LogContext, configure_console_logging, get_logger, log_debug, log_error, log_info, log_warning
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamChunkCodec
from audio_chat.stream.service import StreamNotOpenError

AUDIO_CHAT_SERVER_KEY = web.AppKey("audio_chat_server", object)
AUDIO_CHAT_SWEEPER_TASK_KEY = web.AppKey("audio_chat_sweeper_task", asyncio.Task)
QUIET_CONTROL_EVENTS = {"control.device.heartbeat.received"}


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
        if event.event_name not in QUIET_CONTROL_EVENTS:
            log_debug(
                get_logger("audio_chat.server"),
                f"下发控制事件 {event.event_name}",
                LogContext(
                    user_id=event.user_id,
                    session_id=event.session_id,
                    device_id=self.device_id,
                    stream_id=event.stream_id,
                    event=event.event_name,
                    fields={"stream_type": event.stream_type},
                ),
            )
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
        self.logger = get_logger("audio_chat.server")

    def create_web_app(self) -> web.Application:
        """创建 aiohttp 应用。

        主要逻辑：注册 HTTP debug 路由和两条 WebSocket 路由。
        参数：无。
        返回值：`web.Application`。
        异常情况：无。
        """
        app = web.Application()
        app[AUDIO_CHAT_SERVER_KEY] = self
        app.router.add_get("/api/health", self.health)
        app.router.add_get("/api/debug/devices", self.debug_devices)
        app.router.add_get("/api/debug/devices/{device_id}", self.debug_device)
        app.router.add_get("/api/debug/users/{user_id}", self.debug_user)
        app.router.add_get("/api/debug/playback", self.debug_playback)
        app.router.add_get("/ws/control", self.control_ws)
        app.router.add_get("/ws/stream", self.stream_ws)
        app.on_startup.append(self._on_startup)
        app.on_cleanup.append(self._on_cleanup)
        return app

    async def _on_startup(self, app: web.Application) -> None:
        """注册 server 后台清理任务。

        主要逻辑：周期性触发 heartbeat timeout、stream idle 和 audio session max duration
        清理；业务逻辑仍集中在 `AudioChatApp.run_maintenance_once()`。
        """

        app[AUDIO_CHAT_SWEEPER_TASK_KEY] = asyncio.create_task(self._sweeper_loop())

    async def _on_cleanup(self, app: web.Application) -> None:
        """停止 server 后台清理任务。"""

        task = app.get(AUDIO_CHAT_SWEEPER_TASK_KEY)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _sweeper_loop(self) -> None:
        """后台清理循环。"""

        interval = max(0.1, float(self.audio_app.config.control_heartbeat_check_interval_seconds))
        while True:
            self.audio_app.run_maintenance_once()
            await asyncio.sleep(interval)

    async def health(self, _request: web.Request) -> web.Response:
        """返回服务健康状态。

        主要逻辑：用于 preflight 和本地联调确认 server 已启动。
        参数：aiohttp request。
        返回值：JSON response。
        异常情况：无。
        """
        payload = {"status": "ok", "protocol_version": "audio-chat.v1"}
        if self.audio_app.config.app_name:
            payload["app_name"] = self.audio_app.config.app_name
        return web.json_response(payload)

    async def debug_devices(self, _request: web.Request) -> web.Response:
        """返回设备连接快照。

        主要逻辑：读取 Control Service 的 debug snapshot，包含 connection_id、last_seen_at、
        connection_state、properties 和 routes。
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

    async def debug_playback(self, _request: web.Request) -> web.Response:
        """返回播放仲裁快照。"""

        return web.json_response(self.audio_app.output_service.debug_snapshot())

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
        peer = request.remote or "-"
        log_info(self.logger, "控制 WebSocket 已连接", LogContext(fields={"peer": peer}))
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
                    self._validate_device_session_alias(event)
                    if event.event_name not in QUIET_CONTROL_EVENTS:
                        log_debug(
                            self.logger,
                            f"收到控制事件 {event.event_name}",
                            LogContext(
                                user_id=event.user_id,
                                session_id=event.session_id,
                                device_id=event.producer_id,
                                stream_id=event.stream_id,
                                event=event.event_name,
                                fields={"stream_type": event.stream_type},
                            ),
                        )
                    if event.event_name == "control.device.register.requested":
                        device_id = str(event.payload.get("device_id") or event.producer_id)
                        pending_connection = NetworkDeviceConnection(device_id=device_id, loop=loop)
                        pending_connection.bind_control_ws(ws)
                        registered = self.audio_app.register_device(event, pending_connection)
                        await ws.send_str(json.dumps(registered.to_dict(), ensure_ascii=False))
                        if registered.event_name == "control.device.registered":
                            pending_connection.connection_id = registered.payload.get("connection_id")
                            self.connections[device_id] = pending_connection
                            connection = pending_connection
                            sender_task = asyncio.create_task(sender(connection))
                            log_info(
                                self.logger,
                                "设备注册完成",
                                LogContext(
                                    user_id=event.user_id,
                                    device_id=device_id,
                                    event=registered.event_name,
                                    fields={
                                        "connection_id": pending_connection.connection_id,
                                        "client_type": event.payload.get("client_type"),
                                    },
                                ),
                            )
                    else:
                        self.audio_app.publish_control_event(event)
                except Exception as exc:
                    log_error(
                        self.logger,
                        f"控制事件处理失败 {type(exc).__name__}: {exc}",
                        LogContext(
                            user_id=event.user_id if event else None,
                            session_id=event.session_id if event else None,
                            device_id=event.producer_id if event else None,
                        ),
                    )
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
                log_info(
                    self.logger,
                    "控制 WebSocket 已断开",
                    LogContext(device_id=connection.device_id, fields={"connection_id": connection.connection_id}),
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
        log_info(self.logger, "Stream WebSocket 已连接", LogContext(device_id=device_id))
        reported_errors: set[str] = set()
        suppressed_errors: dict[str, int] = {}
        received_count = 0

        async def sender() -> None:
            while not ws.closed:
                raw = await connection.stream_queue.get()
                await ws.send_bytes(raw)

        sender_task = asyncio.create_task(sender())
        try:
            async for message in ws:
                if message.type != WSMsgType.BINARY:
                    continue
                chunk = None
                try:
                    chunk = StreamChunkCodec.decode(message.data)
                    if chunk.session_id != device_id:
                        raise ValueError("stream chunk session_id must equal device_id")
                    received_count += 1
                    self.audio_app.write_input_chunk(chunk)
                except Exception as exc:
                    if chunk is not None:
                        user_id = chunk.user_id
                        session_id = chunk.session_id
                        stream_id = chunk.stream_id
                        stream_type = chunk.stream_type
                        seq = chunk.seq
                    else:
                        user_id = "unknown"
                        session_id = None
                        stream_id = None
                        stream_type = None
                        seq = None
                    dedupe_key = f"{device_id}:{stream_id}:{type(exc).__name__}:{str(exc)}"
                    if dedupe_key in reported_errors:
                        suppressed_errors[dedupe_key] = suppressed_errors.get(dedupe_key, 0) + 1
                        continue
                    reported_errors.add(dedupe_key)
                    log_method = log_warning if isinstance(exc, StreamNotOpenError) else log_error
                    log_method(
                        self.logger,
                        f"Stream chunk 处理失败 {type(exc).__name__}: {exc}",
                        LogContext(
                            user_id=user_id,
                            session_id=session_id,
                            device_id=device_id,
                            stream_id=stream_id,
                            fields={
                                "stream_type": stream_type,
                                "seq": seq,
                                "note": "同类错误后续会被折叠到断开摘要" if isinstance(exc, StreamNotOpenError) else None,
                            },
                        ),
                    )
                    error = Event(
                        event_name="system.error.raised",
                        user_id=user_id,
                        producer_id="server-main",
                        session_id=session_id,
                        stream_id=stream_id,
                        stream_type=stream_type,
                        payload={
                            "message": str(exc),
                            "error_type": type(exc).__name__,
                            "transport": "stream_ws",
                            "device_id": device_id,
                            "seq": seq,
                            "severity": "warning" if isinstance(exc, StreamNotOpenError) else "error",
                        },
                    )
                    self.audio_app.recorder.record_event(error)
                    self.audio_app.recorder.record_system_event(error.to_dict())
                    await ws.send_str(json.dumps(error.to_dict(), ensure_ascii=False))
        finally:
            for dedupe_key, count in suppressed_errors.items():
                log_warning(
                    self.logger,
                    "Stream chunk 重复错误已折叠",
                    LogContext(device_id=device_id, fields={"dedupe_key": dedupe_key, "suppressed_count": count}),
                )
            sender_task.cancel()
            log_info(self.logger, "Stream WebSocket 已断开", LogContext(device_id=device_id, fields={"received_chunks": received_count}))
        return ws

    @staticmethod
    def _validate_device_session_alias(event: Event) -> None:
        """校验端侧事件不再携带独立 session_id。

        主要逻辑：新版协议把设备作为唯一运行标识；端侧生产的事件如果仍携带
        `session_id`，其值必须等于 `producer_id`，避免重新引入独立会话概念。
        参数：`event` 为端侧控制事件。
        返回值：无。
        异常情况：旧独立 session_id 时抛出 ValueError。
        """

        if event.producer_id == SERVER_PRODUCER_ID:
            return
        if event.session_id and event.session_id != event.producer_id:
            raise ValueError("event session_id must equal producer_id device_id")

    @staticmethod
    def _error_event(exc: Exception, *, event: Event | None, raw: str) -> Event:
        user_id = event.user_id if event is not None else "unknown"
        producer_id = event.producer_id if event is not None else "unknown"
        return Event(
            event_name="system.error.raised",
            user_id=user_id,
            producer_id="server-main",
            session_id=event.session_id if event is not None else None,
            stream_id=event.stream_id if event is not None else None,
            stream_type=event.stream_type if event is not None else None,
            payload={
                "message": str(exc),
                "error_type": type(exc).__name__,
                "source_producer_id": producer_id,
                "source_event": event.event_name if event is not None else None,
                "raw_event": raw[:512],
                "transport": "control_ws",
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
    parser.add_argument("--config", default="")
    parser.add_argument("--app-name", default="", help="应用名称，对应 app-examples/<app-name>")
    parser.add_argument("--app-root", default="app-examples", help="应用根目录，默认 app-examples")
    parser.add_argument("--app-module", default="")
    args = parser.parse_args(argv)

    if args.app_name:
        config, launch = load_app_config(args.app_name, app_root=args.app_root)
        resolved_config_path = str(launch.config_path)
    else:
        config_path = args.config or "app-examples/for-blind-app/server.yaml"
        config, launch = load_config_as_app(config_path)
        resolved_config_path = str(launch.config_path)
    configure_console_logging(config.log_level)
    logger = get_logger("audio_chat.server")
    log_info(
        logger,
        "audio-chat server 启动",
        LogContext(
            fields={
                "config": resolved_config_path,
                "app_name": config.app_name,
                "app_dir": config.app_dir,
                "config_path": config.config_path or resolved_config_path,
                "host": config.server_host,
                "port": config.server_port,
                "runs_root": config.runs_root,
                "agent_mode": config.agent_mode,
                "realtime_provider": config.realtime_provider,
                "text_model_provider": config.text_model_provider,
            }
        ),
    )
    audio_app = AudioChatApp(config)
    _load_app_module(args.app_module, audio_app)
    server = AudioChatHttpServer(audio_app)
    web.run_app(server.create_web_app(), host=config.server_host, port=config.server_port)
