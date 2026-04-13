"""控制连接运行时。"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from infra.config import ServerSettings
from infra.errors import ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_info
from protocol.codec.json_codec import JsonMessageCodec
from protocol.messages.control_message import ControlMessage, Endpoint
from protocol.utils.message_factory import create_control_message
from runtime import VoiceRuntime
from runtime.voice_runtime import SpeechRecognitionClient, VoiceModelClient


@dataclass(slots=True)
class ControlConnection:
    """控制连接对象。"""

    connection_id: str
    peer: str
    send_text: Callable[[str], None]
    close_transport: Callable[[int, str], None]
    device_id: str | None = None
    device_type: str = "glass"
    registered: bool = False
    voice_opened: bool = False
    session_id: str | None = None
    last_seen_monotonic: float = field(default_factory=time.monotonic)
    last_heartbeat_monotonic: float = field(default_factory=time.monotonic)
    closed: bool = False
    send_lock: threading.Lock = field(default_factory=threading.Lock)

    def touch(self) -> None:
        """刷新最近活跃时间。"""

        now = time.monotonic()
        self.last_seen_monotonic = now
        if self.registered:
            self.last_heartbeat_monotonic = now

    def touch_heartbeat(self) -> None:
        """刷新心跳时间。"""

        now = time.monotonic()
        self.last_seen_monotonic = now
        self.last_heartbeat_monotonic = now


class ControlRuntime:
    """控制面运行时。

    主要功能：
    1. 管理设备控制连接、注册状态和心跳超时。
    2. 路由控制消息到语音运行时。
    3. 对外暴露运行态快照，服务联调与验收观察。
    """

    def __init__(
        self,
        settings: ServerSettings,
        *,
        model_client: VoiceModelClient | None = None,
        asr_client: SpeechRecognitionClient | None = None,
    ) -> None:
        self._settings = settings
        self._codec = JsonMessageCodec()
        self._logger = get_logger("server.control")
        self._lock = threading.Lock()
        self._connections: dict[str, ControlConnection] = {}
        self._device_connections: dict[str, ControlConnection] = {}
        self._voice_runtime = VoiceRuntime(
            settings=settings,
            send_control_message=self._send_message_to_device,
            model_client=model_client,
            asr_client=asr_client,
        )
        self._stop_event = threading.Event()
        self._sweeper_thread = threading.Thread(
            target=self._heartbeat_sweeper,
            name="control-heartbeat-sweeper",
            daemon=True,
        )
        self._started = False

    def start(self) -> None:
        """启动后台心跳清理线程。"""

        if self._started:
            return
        self._started = True
        self._sweeper_thread.start()

    def stop(self) -> None:
        """停止运行时并关闭所有连接。"""

        self._stop_event.set()
        if self._started:
            self._sweeper_thread.join(timeout=2)

        with self._lock:
            connections = list(self._connections.values())

        for connection in connections:
            self.close_connection(connection, code=1001, reason="server shutdown")

    def open_connection(
        self,
        *,
        peer: str,
        send_text: Callable[[str], None],
        close_transport: Callable[[int, str], None],
    ) -> ControlConnection:
        """注册一条新建的传输连接。"""

        connection = ControlConnection(
            connection_id=f"conn_{uuid.uuid4().hex}",
            peer=peer,
            send_text=send_text,
            close_transport=close_transport,
        )
        with self._lock:
            self._connections[connection.connection_id] = connection
        return connection

    def on_transport_closed(self, connection: ControlConnection) -> None:
        """处理底层连接关闭。"""

        removed_device_id: str | None = None
        with self._lock:
            connection.closed = True
            self._connections.pop(connection.connection_id, None)
            if connection.device_id:
                current = self._device_connections.get(connection.device_id)
                if current is connection:
                    self._device_connections.pop(connection.device_id, None)
                    removed_device_id = connection.device_id
        self._voice_runtime.on_control_connection_closed(removed_device_id)

    def close_connection(self, connection: ControlConnection, *, code: int, reason: str) -> None:
        """关闭一条控制连接。"""

        with self._lock:
            if connection.closed:
                return
            connection.closed = True

        try:
            connection.close_transport(code, reason)
        finally:
            self.on_transport_closed(connection)

    def handle_text(self, connection: ControlConnection, text: str) -> None:
        """处理一条文本控制消息。"""

        message = self._codec.decode(text)
        context = LogContext(
            trace_id=message.trace_id,
            session_id=message.session_id,
            device_id=connection.device_id,
            message_id=message.message_id,
        )
        if self._should_log_control_message(message.name):
            log_info(self._logger, f"收到控制消息: {message.name}", context)
        connection.last_seen_monotonic = time.monotonic()

        if not connection.registered and message.name != "device.register":
            raise build_error(
                ErrorCode.UNAUTHORIZED,
                "设备尚未注册，当前消息不允许进入业务态",
                details={"message_name": message.name},
            )

        if message.name == "device.register":
            self._handle_register(connection, message)
            return
        if message.name == "device.heartbeat":
            self._handle_heartbeat(connection, message)
            return
        if message.name == "voice.session.opened":
            self._handle_voice_session_opened(connection, message)
            return
        if message.name == "sensor.audio.segment.started":
            self._handle_segment_started(connection, message)
            return
        if message.name == "sensor.audio.segment.finished":
            self._handle_segment_finished(connection, message)
            return
        if message.name == "actuator.audio.started":
            self._handle_actuator_audio_started(connection, message)
            return
        if message.name == "actuator.audio.finished":
            self._handle_actuator_audio_finished(connection, message)
            return

        log_info(self._logger, f"忽略未支持控制消息: {message.name}", context)

    def build_runtime_snapshot(self) -> dict[str, object]:
        """返回当前运行态快照。"""

        with self._lock:
            connections = list(self._connections.values())

        now = time.monotonic()
        online_devices = sorted(
            connection.device_id
            for connection in connections
            if connection.registered and not connection.closed and connection.device_id
        )
        connection_items = []
        for connection in sorted(connections, key=lambda item: item.connection_id):
            connection_items.append(
                {
                    "connection_id": connection.connection_id,
                    "device_id": connection.device_id,
                    "peer": connection.peer,
                    "registered": connection.registered,
                    "session_id": connection.session_id,
                    "voice_opened": connection.voice_opened,
                    "last_seen_ms_ago": int((now - connection.last_seen_monotonic) * 1000),
                    "heartbeat_age_ms": int((now - connection.last_heartbeat_monotonic) * 1000),
                }
            )
        return {
            "online_device_count": len(online_devices),
            "online_devices": online_devices,
            "connections": connection_items,
            "voice_sessions": self._voice_runtime.build_runtime_snapshot(),
        }

    def _handle_register(self, connection: ControlConnection, message: ControlMessage) -> None:
        payload = message.payload
        device_id = str(payload.get("device_id", "")).strip()
        device_type = str(payload.get("device_type", "glass")).strip() or "glass"
        auth = payload.get("auth", {})
        auth_mode = str(auth.get("mode", "")).strip() if isinstance(auth, dict) else ""
        pair_token = str(auth.get("pair_token", "")).strip() if isinstance(auth, dict) else ""

        if not device_id:
            self._send_register_failed(
                connection=connection,
                device_id="",
                reason="device_id 不能为空",
                code=ErrorCode.INVALID_MESSAGE,
            )
            return
        if auth_mode != "pair_token":
            self._send_register_failed(
                connection=connection,
                device_id=device_id,
                reason="仅支持 mode=pair_token",
                code=ErrorCode.UNAUTHORIZED,
            )
            return

        expected = self._settings.parse_device_token_map().get(device_id)
        if not expected or expected != pair_token:
            self._send_register_failed(
                connection=connection,
                device_id=device_id,
                reason="pair_token 校验失败",
                code=ErrorCode.UNAUTHORIZED,
            )
            return

        old_connection: ControlConnection | None = None
        with self._lock:
            current = self._device_connections.get(device_id)
            if current and current is not connection:
                old_connection = current
            connection.device_id = device_id
            connection.device_type = device_type
            connection.registered = True
            connection.voice_opened = False
            connection.session_id = f"sess_{uuid.uuid4().hex[:12]}"
            connection.touch_heartbeat()
            self._device_connections[device_id] = connection
            self._voice_runtime.open_session(
                device_id=device_id,
                device_type=device_type,
                session_id=connection.session_id,
            )

        if old_connection is not None:
            log_info(
                self._logger,
                f"检测到同设备重连，关闭旧连接: device_id={device_id}",
                LogContext(device_id=device_id),
            )
            self.close_connection(old_connection, code=4001, reason="replaced by new connection")

        self._send_message(
            connection,
            create_control_message(
                semantic="notify",
                name="device.registered",
                source=self._server_endpoint(),
                target=self._device_endpoint(device_id, device_type),
                payload={
                    "device_id": device_id,
                    "heartbeat_interval_ms": self._settings.heartbeat_interval_ms,
                },
                trace_id=message.trace_id,
            ),
        )
        self._send_message(
            connection,
            create_control_message(
                semantic="request",
                name="voice.session.open",
                source=self._server_endpoint(),
                target=self._device_endpoint(device_id, device_type),
                payload=self._voice_runtime.build_open_payload(),
                trace_id=message.trace_id,
                session_id=connection.session_id,
            ),
        )

    def _handle_heartbeat(self, connection: ControlConnection, message: ControlMessage) -> None:
        payload_device_id = str(message.payload.get("device_id", "")).strip()
        if payload_device_id and connection.device_id and payload_device_id != connection.device_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "heartbeat.device_id 与当前连接不一致",
                details={
                    "payload_device_id": payload_device_id,
                    "connection_device_id": connection.device_id,
                },
            )
        connection.touch_heartbeat()

    def _handle_voice_session_opened(self, connection: ControlConnection, message: ControlMessage) -> None:
        if not connection.session_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "当前连接不存在待确认的 session_id",
            )
        if message.session_id != connection.session_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "voice.session.opened 的 session_id 不匹配",
                details={
                    "expected_session_id": connection.session_id,
                    "actual_session_id": message.session_id,
                },
            )
        connection.touch_heartbeat()
        connection.voice_opened = True
        self._voice_runtime.on_voice_session_opened(
            device_id=connection.device_id or "",
            session_id=message.session_id,
        )

    def _handle_segment_started(self, connection: ControlConnection, message: ControlMessage) -> None:
        connection.last_seen_monotonic = time.monotonic()
        segment_id = str(message.payload.get("segment_id", "")).strip()
        stream_id = str(message.payload.get("stream_id", "")).strip()
        self._voice_runtime.on_segment_started(
            device_id=connection.device_id or "",
            session_id=message.session_id or "",
            payload=message.payload,
        )
        log_info(
            self._logger,
            f"收到语音唤醒状态上报: segment_id={segment_id or '<none>'} stream_id={stream_id or '<none>'}",
            LogContext(
                trace_id=message.trace_id,
                session_id=message.session_id,
                device_id=connection.device_id,
                message_id=message.message_id,
            ),
        )

    def _handle_segment_finished(self, connection: ControlConnection, message: ControlMessage) -> None:
        connection.last_seen_monotonic = time.monotonic()
        self._voice_runtime.on_segment_finished(
            device_id=connection.device_id or "",
            session_id=message.session_id or "",
            payload=message.payload,
        )

    def _handle_actuator_audio_started(self, connection: ControlConnection, message: ControlMessage) -> None:
        stream_id = str(message.payload.get("stream_id") or message.stream_id or "").strip()
        self._voice_runtime.on_playback_started(
            device_id=connection.device_id or "",
            session_id=message.session_id or "",
            stream_id=stream_id,
        )

    def _handle_actuator_audio_finished(self, connection: ControlConnection, message: ControlMessage) -> None:
        stream_id = str(message.payload.get("stream_id") or message.stream_id or "").strip()
        self._voice_runtime.on_playback_finished(
            device_id=connection.device_id or "",
            session_id=message.session_id or "",
            stream_id=stream_id,
        )

    def _send_register_failed(
        self,
        *,
        connection: ControlConnection,
        device_id: str,
        reason: str,
        code: ErrorCode,
    ) -> None:
        target_device_id = device_id or "unknown-device"
        self._send_message(
            connection,
            create_control_message(
                semantic="notify",
                name="device.register.failed",
                source=self._server_endpoint(),
                target=self._device_endpoint(target_device_id, "glass"),
                payload={
                    "device_id": device_id,
                    "error": {
                        "code": str(code),
                        "message": reason,
                        "retryable": False,
                        "details": {},
                    },
                },
            ),
        )
        self.close_connection(connection, code=4003, reason="register failed")

    def _send_message(self, connection: ControlConnection, message: ControlMessage) -> None:
        if connection.closed:
            return
        raw = self._codec.encode(message).decode("utf-8")
        with connection.send_lock:
            connection.send_text(raw)
        if self._should_log_control_message(message.name):
            log_info(
                self._logger,
                f"已发送控制消息: {message.name}",
                LogContext(
                    trace_id=message.trace_id,
                    session_id=message.session_id,
                    device_id=connection.device_id,
                    message_id=message.message_id,
                ),
            )

    def _send_message_to_device(
        self,
        device_id: str,
        semantic: str,
        name: str,
        session_id: str,
        payload: dict[str, object],
    ) -> None:
        with self._lock:
            connection = self._device_connections.get(device_id)
        if connection is None or connection.closed:
            raise build_error(
                ErrorCode.STREAM_NOT_FOUND,
                "目标设备控制连接不存在",
                details={"device_id": device_id, "name": name},
            )

        stream_id = None
        if isinstance(payload.get("stream_id"), str):
            stream_id = str(payload["stream_id"])
        self._send_message(
            connection,
            create_control_message(
                semantic=semantic,
                name=name,
                source=self._server_endpoint(),
                target=self._device_endpoint(connection.device_id or device_id, connection.device_type),
                payload=payload,
                session_id=session_id,
                stream_id=stream_id,
            ),
        )

    def _heartbeat_sweeper(self) -> None:
        sweep_interval = max(0.05, min(1.0, self._settings.heartbeat_timeout_ms / 1000 / 3))
        while not self._stop_event.wait(sweep_interval):
            now = time.monotonic()
            stale_connections: list[ControlConnection] = []
            with self._lock:
                for connection in self._connections.values():
                    if connection.closed or not connection.registered:
                        continue
                    age_ms = int((now - connection.last_heartbeat_monotonic) * 1000)
                    if age_ms > self._settings.heartbeat_timeout_ms:
                        stale_connections.append(connection)

            for connection in stale_connections:
                log_info(
                    self._logger,
                    "设备心跳超时，关闭连接",
                    LogContext(device_id=connection.device_id, session_id=connection.session_id),
                )
                self.close_connection(connection, code=4000, reason="heartbeat timeout")

    def _server_endpoint(self) -> Endpoint:
        return Endpoint(
            device_id=self._settings.server_device_id,
            device_type="server",
            module="server-api",
        )

    @staticmethod
    def _device_endpoint(device_id: str, device_type: str) -> Endpoint:
        return Endpoint(
            device_id=device_id,
            device_type=device_type,
            module="glass-api",
        )

    @property
    def voice_runtime(self) -> VoiceRuntime:
        return self._voice_runtime

    @staticmethod
    def _should_log_control_message(name: str) -> bool:
        """判断控制消息是否需要输出常规成功日志。

        主要逻辑：
        1. 对高频正常心跳消息默认静默，避免刷屏。
        2. 其它消息仍然保持原有日志粒度。

        参数：
        1. `name`：控制消息名。

        返回值：
        1. `True` 表示打印常规日志；`False` 表示静默。
        """

        return name != "device.heartbeat"
