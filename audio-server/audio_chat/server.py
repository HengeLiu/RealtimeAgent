from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import WSMsgType, web

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.app_loader import load_app_config, load_config_as_app
from audio_chat.auth import setup_auth_routes
from audio_chat.admin import setup_admin_routes
from audio_chat.observability import (
    LogContext,
    configure_console_logging,
    get_logger,
    log_debug,
    log_error,
    log_info,
    log_warning,
    should_log_control_event_to_terminal,
)
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamChunkCodec
from audio_chat.stream.service import StreamNotOpenError

AUDIO_CHAT_SERVER_KEY = web.AppKey("audio_chat_server", object)
AUDIO_CHAT_SWEEPER_TASK_KEY = web.AppKey("audio_chat_sweeper_task", asyncio.Task)


def _elapsed_ms(start: object, end: object) -> int | None:
    """计算两个单调时间点之间的毫秒差。

    主要逻辑：日志埋点可能遇到缺失字段，缺失或无法转换时返回 `None`，
    避免排障日志影响主链路。
    参数：`start` 和 `end` 为 `time.monotonic()` 风格的秒级时间。
    返回值：整数毫秒或 `None`。
    异常情况：类型转换失败时吞掉异常并返回 `None`。
    """

    if start is None or end is None:
        return None
    try:
        return int((float(end) - float(start)) * 1000)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class QueuedStreamPayload:
    """下行 stream WebSocket 队列项。

    主要功能：在原始二进制 payload 外携带入队时间和 stream 摘要，便于只打印
    首包和汇总级发送日志，而不恢复逐 chunk 噪音。
    主要属性：`raw` 是待发送二进制；`meta` 保存 stream_id、seq、payload_size 等。
    """

    raw: bytes
    meta: dict[str, Any]


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
    stream_queue: asyncio.Queue[QueuedStreamPayload] = field(default_factory=asyncio.Queue)
    connection_id: str | None = None
    _control_ws: web.WebSocketResponse | None = None
    _stream_ws: web.WebSocketResponse | None = None
    _stream_send_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    _stream_send_lock: threading.Lock = field(default_factory=threading.Lock)

    def bind_control_ws(self, ws: web.WebSocketResponse) -> None:
        """绑定控制 WebSocket。

        主要逻辑：保存当前 WebSocket，供重连覆盖和关闭原连接使用。
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
        if should_log_control_event_to_terminal(event):
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
        if event.event_name == "stream.output.close.requested" and event.stream_type == "actuator.speaker":
            self._log_stream_send_summary(event)
        self.loop.call_soon_threadsafe(self.event_queue.put_nowait, event)

    def push_stream_chunk(self, chunk: object) -> None:
        """投递下行 stream chunk。

        主要逻辑：把 `StreamChunk` 编码成协议二进制后写入 stream 队列。
        参数：`chunk` 为 `StreamChunk` 对象。
        返回值：无。
        异常情况：对象不符合编码协议时会在调用方线程抛出异常。
        """
        raw = StreamChunkCodec.encode(chunk)  # type: ignore[arg-type]
        stream_id = str(getattr(chunk, "stream_id", "") or "")
        stream_type = str(getattr(chunk, "stream_type", "") or "")
        payload_size = len(getattr(chunk, "payload", b"") or b"")
        meta = {
            "user_id": getattr(chunk, "user_id", None),
            "session_id": getattr(chunk, "session_id", None),
            "stream_id": stream_id,
            "stream_type": stream_type,
            "seq": getattr(chunk, "seq", None),
            "payload_size": payload_size,
            "enqueued_at": time.monotonic(),
        }
        if stream_type == "actuator.speaker":
            self._record_stream_payload_enqueued(meta)
        self.loop.call_soon_threadsafe(self.stream_queue.put_nowait, QueuedStreamPayload(raw=raw, meta=meta))

    def mark_stream_payload_sent(self, meta: dict[str, Any], *, sent_at: float) -> None:
        """记录下行 stream chunk 已完成 WebSocket send。

        主要逻辑：只对 speaker output 打首包发送日志；全量统计在 close.requested 时
        统一输出，避免每个 chunk 刷屏。
        参数：`meta` 是入队时记录的摘要；`sent_at` 是 send_bytes 返回后的时间。
        返回值：无。
        异常情况：无。
        """

        if meta.get("stream_type") != "actuator.speaker":
            return
        stream_id = str(meta.get("stream_id") or "")
        if not stream_id:
            return
        with self._stream_send_lock:
            stats = self._stream_send_stats.setdefault(stream_id, {})
            stats["sent_count"] = int(stats.get("sent_count") or 0) + 1
            stats["sent_bytes"] = int(stats.get("sent_bytes") or 0) + int(meta.get("payload_size") or 0)
            stats["last_sent_at"] = sent_at
            stats["last_sent_seq"] = meta.get("seq")
            first_sent = "first_sent_at" not in stats
            if first_sent:
                stats["first_sent_at"] = sent_at
        if first_sent:
            enqueued_at = meta.get("enqueued_at")
            log_info(
                get_logger("audio_chat.server"),
                "下行音频首包已发送",
                LogContext(
                    user_id=str(meta.get("user_id") or ""),
                    session_id=str(meta.get("session_id") or ""),
                    device_id=self.device_id,
                    stream_id=stream_id,
                    event="stream.output.first_chunk.sent",
                    fields={
                        "seq": meta.get("seq"),
                        "payload_size": meta.get("payload_size"),
                        "queue_wait_ms": _elapsed_ms(enqueued_at, sent_at),
                    },
                ),
            )

    def close(self, *, reason: str) -> None:
        """关闭当前网络连接。

        主要逻辑：设备同 `device_id` 重连时由 Control Service 调用，关闭原控制和 stream
        WebSocket，避免同一设备多连接同时收下行数据。
        参数：`reason` 为关闭原因。
        返回值：无。
        异常情况：WebSocket 已关闭时忽略。
        """
        for ws in (self._control_ws, self._stream_ws):
            if ws is not None and not ws.closed:
                self.loop.call_soon_threadsafe(asyncio.create_task, ws.close(message=reason.encode("utf-8")))

    def _record_stream_payload_enqueued(self, meta: dict[str, Any]) -> None:
        """记录 speaker output chunk 入队。"""

        stream_id = str(meta.get("stream_id") or "")
        if not stream_id:
            return
        now = float(meta.get("enqueued_at") or time.monotonic())
        with self._stream_send_lock:
            stats = self._stream_send_stats.setdefault(
                stream_id,
                {
                    "user_id": meta.get("user_id"),
                    "session_id": meta.get("session_id"),
                    "first_enqueued_at": now,
                    "first_enqueued_seq": meta.get("seq"),
                    "enqueued_count": 0,
                    "enqueued_bytes": 0,
                },
            )
            stats["enqueued_count"] = int(stats.get("enqueued_count") or 0) + 1
            stats["enqueued_bytes"] = int(stats.get("enqueued_bytes") or 0) + int(meta.get("payload_size") or 0)
            stats["last_enqueued_at"] = now
            stats["last_enqueued_seq"] = meta.get("seq")
            first_enqueued = stats["enqueued_count"] == 1
        if first_enqueued:
            log_info(
                get_logger("audio_chat.server"),
                "下行音频首包入队",
                LogContext(
                    user_id=str(meta.get("user_id") or ""),
                    session_id=str(meta.get("session_id") or ""),
                    device_id=self.device_id,
                    stream_id=stream_id,
                    event="stream.output.first_chunk.enqueued",
                    fields={
                        "seq": meta.get("seq"),
                        "payload_size": meta.get("payload_size"),
                    },
                ),
            )

    def _log_stream_send_summary(self, event: Event) -> None:
        """在 output close 时打印下行发送汇总。"""

        stream_id = event.stream_id or ""
        if not stream_id:
            return
        with self._stream_send_lock:
            stats = dict(self._stream_send_stats.pop(stream_id, {}))
        if not stats:
            return
        first_enqueued_at = stats.get("first_enqueued_at")
        first_sent_at = stats.get("first_sent_at")
        last_sent_at = stats.get("last_sent_at")
        last_enqueued_at = stats.get("last_enqueued_at")
        log_info(
            get_logger("audio_chat.server"),
            "下行音频发送汇总",
            LogContext(
                user_id=event.user_id,
                session_id=event.session_id,
                device_id=self.device_id,
                stream_id=stream_id,
                event="stream.output.send.summary",
                fields={
                    "enqueued_count": stats.get("enqueued_count"),
                    "enqueued_bytes": stats.get("enqueued_bytes"),
                    "sent_count": stats.get("sent_count"),
                    "sent_bytes": stats.get("sent_bytes"),
                    "first_queue_to_first_send_ms": _elapsed_ms(first_enqueued_at, first_sent_at),
                    "first_queue_to_last_send_ms": _elapsed_ms(first_enqueued_at, last_sent_at),
                    "last_enqueue_to_close_request_ms": _elapsed_ms(last_enqueued_at, time.monotonic()),
                    "first_seq": stats.get("first_enqueued_seq"),
                    "last_seq": stats.get("last_sent_seq") or stats.get("last_enqueued_seq"),
                },
            ),
        )


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
        app.router.add_get("/api/debug/tasks", self.debug_tasks)
        app.router.add_get("/ws/control", self.control_ws)
        app.router.add_get("/ws/stream", self.stream_ws)
        
        setup_auth_routes(app)
        setup_admin_routes(app)
        
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

    async def debug_tasks(self, request: web.Request) -> web.Response:
        """返回 Task Core 调试快照。

        主要逻辑：列出 TaskRef、最近 TaskSignal 和调度等待项，用于排查 Task actor
        是否启动、是否进入终态以及最近一次 dispatch 结果。
        参数：可选 query `user_id` 和 `include_terminal`。
        返回值：JSON response。
        异常情况：无。
        """

        user_id = request.query.get("user_id") or None
        include_terminal = request.query.get("include_terminal", "true").lower() not in {"0", "false", "no"}
        tasks = []
        for ref in self.audio_app.task_engine.list_tasks(user_id=user_id, include_terminal=include_terminal):
            signals = self.audio_app.task_engine.store.signals_for_task(ref.task_id)
            tasks.append(
                {
                    "task_id": ref.task_id,
                    "task_type": ref.task_type,
                    "state": ref.state,
                    "summary": ref.summary,
                    "metadata": dict(ref.metadata),
                    "recent_signals": [
                        {
                            "signal_name": signal.signal_name,
                            "payload": dict(signal.payload),
                            "created_at": signal.created_at,
                        }
                        for signal in signals[-5:]
                    ],
                }
            )
        return web.json_response(
            {
                "tasks": tasks,
                "scheduled_signals": self.audio_app.task_engine.list_scheduled_signals(),
            }
        )

    async def control_ws(self, request: web.Request) -> web.WebSocketResponse:
        """处理控制 WebSocket。

        主要逻辑：接收端侧 Event JSON；注册事件会绑定连接，其他事件进入
        `AudioChatApp.publish_control_event()`。下行事件由 sender task 从队列发送。
        参数：aiohttp request。
        返回值：WebSocket response。
        异常情况：JSON 或协议错误会向端侧发送 `system.error.raised` 后继续等待下一条。
        """
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            from audio_chat.auth.jwt_handler import verify_token
            token_data = verify_token(token, token_type="access")
            if token_data is None:
                log_warning(self.logger, "控制 WebSocket Token 验证失败", LogContext(fields={"token": token[:20]}))
                ws = web.WebSocketResponse()
                await ws.prepare(request)
                await ws.close(code=4001, message=b"Invalid token")
                return ws
            log_info(self.logger, "控制 WebSocket Token 验证成功", LogContext(fields={"user_id": token_data.user_id}))
        
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
                    if should_log_control_event_to_terminal(event):
                        log_debug(
                            self.logger,
                            f"收到控制事件 {event.event_name}",
                            LogContext(
                                user_id=event.user_id,
                                session_id=event.session_id,
                                device_id=event.producer_id,
                                stream_id=event.stream_id,
                                event=event.event_name,
                                trace_id=event.trace_id,
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
                self.audio_app.mark_device_connection_offline(
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
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            from audio_chat.auth.jwt_handler import verify_token
            token_data = verify_token(token, token_type="access")
            if token_data is None:
                log_warning(self.logger, "Stream WebSocket Token 验证失败", LogContext(fields={"token": token[:20]}))
                ws = web.WebSocketResponse()
                await ws.prepare(request)
                await ws.close(code=4001, message=b"Invalid token")
                return ws
            log_info(self.logger, "Stream WebSocket Token 验证成功", LogContext(fields={"user_id": token_data.user_id}))
        
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
        background_dispatches: set[asyncio.Task] = set()

        async def sender() -> None:
            while not ws.closed:
                queued = await connection.stream_queue.get()
                await ws.send_bytes(queued.raw)
                connection.mark_stream_payload_sent(queued.meta, sent_at=time.monotonic())

        def finish_background_dispatch(task: asyncio.Task, *, dispatched_chunk) -> None:
            """收口后台 final mic 分发任务。"""

            background_dispatches.discard(task)
            try:
                task.result()
            except Exception as exc:  # noqa: BLE001 - 后台任务不能让异常丢失
                log_error(
                    self.logger,
                    f"后台 Stream chunk 处理失败 {type(exc).__name__}: {exc}",
                    LogContext(
                        user_id=dispatched_chunk.user_id,
                        session_id=dispatched_chunk.session_id,
                        device_id=device_id,
                        stream_id=dispatched_chunk.stream_id,
                        event="stream.background_dispatch.failed",
                    ),
                )
                error = self._error_event(
                    exc,
                    event=Event(
                        event_name="stream.chunk.received",
                        user_id=dispatched_chunk.user_id,
                        producer_id=device_id,
                        session_id=dispatched_chunk.session_id,
                        stream_id=dispatched_chunk.stream_id,
                        stream_type=dispatched_chunk.stream_type,
                    ),
                    raw="background final mic dispatch",
                )
                self.audio_app.recorder.record_event(error)
                self.audio_app.recorder.record_system_event(error.to_dict())

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
                    # Stream Service / Agent Core 是同步入口；放到线程执行，避免接收侧处理
                    # 大量 mic chunk 时饿住同一 aiohttp loop 上的控制/音频下行发送协程。
                    # final mic chunk 会触发 ASR、LLM 和 Tool 调用，继续后台执行，避免 Text
                    # 工具等待端侧资产时阻塞同一条 WebSocket 继续接收图片帧。
                    if chunk.stream_type == "sensor.mic" and chunk.final:
                        task = asyncio.create_task(asyncio.to_thread(self.audio_app.write_input_chunk, chunk))
                        background_dispatches.add(task)
                        task.add_done_callback(lambda item, dispatched_chunk=chunk: finish_background_dispatch(item, dispatched_chunk=dispatched_chunk))
                    else:
                        await asyncio.to_thread(self.audio_app.write_input_chunk, chunk)
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
        异常情况：独立 session_id 时抛出 ValueError。
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
    parser.add_argument("--app-name", default="", help="应用名称，对应 examples/<app-name>/audio-server")
    parser.add_argument("--app-root", default="examples", help="应用根目录，默认 examples")
    parser.add_argument("--app-module", default="")
    args = parser.parse_args(argv)

    if args.app_name:
        config, launch = load_app_config(args.app_name, app_root=args.app_root)
        resolved_config_path = str(launch.config_path)
    else:
        config_path = args.config or "examples/for-blind-app/audio-server/server.yaml"
        config, launch = load_config_as_app(config_path)
        resolved_config_path = str(launch.config_path)
    configure_console_logging(config.log_level, timezone_name=config.log_timezone)
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
                "log_timezone": config.log_timezone,
                "runs_root": config.runs_root,
                "agent_mode": config.agent_mode,
                "realtime_provider": config.realtime_provider,
                "text_provider": config.text_provider,
            }
        ),
    )
    audio_app = AudioChatApp(config)
    _load_app_module(args.app_module, audio_app)
    server = AudioChatHttpServer(audio_app)
    web.run_app(server.create_web_app(), host=config.server_host, port=config.server_port)
