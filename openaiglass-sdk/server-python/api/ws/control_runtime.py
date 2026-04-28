"""控制连接运行时。"""

from __future__ import annotations

import base64
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from agent_core import AgentFacade
from agent_core.camera import CameraCaptureResult, CameraGateway
from infra.config import ServerSettings
from infra.errors import ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug, log_info, log_warning
from protocol.codec.json_codec import JsonMessageCodec
from protocol.messages.control_message import ControlMessage, Endpoint
from protocol.utils.message_factory import create_control_message
from runtime import VoiceRuntime
from runtime.voice_runtime import SpeechRecognitionClient, VoiceModelClient
from backend_task_core import TaskEvent
from openaiglasses.runtime import BackendTaskGatewayAdapter
from openaiglasses.runtime.device_group import DeviceGroupRuntime


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
    camera_sink_ws_uri: str | None = None
    desired_glass_device_id: str | None = None
    desired_phone_device_id: str | None = None
    account_id: str | None = None
    user_id: str | None = None
    mock_capabilities: dict[str, object] = field(default_factory=dict)

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


@dataclass(slots=True)
class PendingCameraCapture:
    """待完成的单次抓拍请求。

    主要功能：
    1. 保存一次抓拍请求的等待状态。
    2. 允许设备回传图片后唤醒阻塞中的 Tool 调用。
    """

    request_id: str
    device_id: str
    session_id: str
    reason: str
    created_at_monotonic: float = field(default_factory=time.monotonic)
    event: threading.Event = field(default_factory=threading.Event)
    result: CameraCaptureResult | None = None
    error: Exception | None = None


class ControlRuntime(CameraGateway):
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
        agent_facade: AgentFacade | None = None,
    ) -> None:
        self._settings = settings
        self._codec = JsonMessageCodec()
        self._logger = get_logger("server.control")
        self._lock = threading.Lock()
        self._connections: dict[str, ControlConnection] = {}
        self._device_connections: dict[str, ControlConnection] = {}
        self._glass_to_phone: dict[str, str] = {}
        self._phone_to_glass: dict[str, str] = {}
        self._device_group_runtime = DeviceGroupRuntime()
        self._active_phone_video_task_ids_by_glass: dict[str, str] = {}
        self._pending_camera_captures: dict[str, PendingCameraCapture] = {}
        self._voice_runtime = VoiceRuntime(
            settings=settings,
            send_control_message=self._send_message_to_device,
            model_client=model_client,
            asr_client=asr_client,
            agent_facade=agent_facade,
        )
        self._device_group_runtime.device_command_adapter = self._send_device_group_command
        self._device_group_runtime.task_runtime = BackendTaskGatewayAdapter(
            task_gateway=self._voice_runtime.agent_facade.get_task_gateway(),
        )
        self._device_group_runtime.bind_mcp_gateway(
            self._voice_runtime.agent_facade.get_tool_registry().get_mcp_gateway(),
            settings=settings,
            session_store=self._voice_runtime.agent_facade.get_session_store(),
        )
        self._voice_runtime.agent_facade.bind_device_state_reader(self.build_runtime_snapshot)
        self._voice_runtime.agent_facade.bind_device_group_context_factory(self.create_device_group_context)
        task_gateway = self._voice_runtime.agent_facade.get_task_gateway()
        if hasattr(task_gateway, "bind_device_groups"):
            task_gateway.bind_device_groups(self._device_group_runtime)
        self._voice_runtime.agent_facade.bind_camera_gateway(self)
        self._voice_runtime.agent_facade.bind_task_event_listener(self._voice_runtime.on_task_event)
        self._voice_runtime.agent_facade.bind_task_event_listener(self._handle_task_event)
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
        self._voice_runtime.agent_facade.shutdown()

        with self._lock:
            connections = list(self._connections.values())
            pending_requests = list(self._pending_camera_captures.values())
            self._pending_camera_captures.clear()

        for pending in pending_requests:
            pending.error = build_error(
                ErrorCode.INTERNAL_ERROR,
                "服务端正在关闭，抓拍请求被中断",
                details={"request_id": pending.request_id},
            )
            pending.event.set()

        for connection in connections:
            self.close_connection(connection, code=1001, reason="server shutdown")

    @property
    def device_group_runtime(self) -> DeviceGroupRuntime:
        """返回设备组运行时。

        主要功能：
        1. 为调试入口、SDK 装配层和集成测试暴露统一设备组能力。
        2. 避免外部代码直接访问私有字段。
        """

        return self._device_group_runtime

    def create_device_group_context(
        self,
        *,
        device_id: str,
        session_id: str | None = None,
        task_id: str | None = None,
    ):
        """创建真实运行时上的 SDK 设备组上下文。

        主要逻辑：
        1. 若未显式提供 `session_id`，则优先复用当前控制连接上的会话编号。
        2. 再委托 `DeviceGroupRuntime` 创建高层上下文。

        参数：
        1. `device_id`：当前设备编号。
        2. `session_id`：可选会话编号。
        3. `task_id`：可选任务编号。

        返回值：
        1. `DeviceGroupContext`。

        异常情况：
        1. 设备不在线或缺少可用会话时抛出结构化错误。
        """

        resolved_session_id = str(session_id or "").strip()
        if not resolved_session_id:
            with self._lock:
                connection = self._device_connections.get(device_id)
            if connection is None or connection.closed or not connection.registered:
                raise build_error(
                    ErrorCode.STREAM_NOT_FOUND,
                    "目标设备当前不在线，无法创建 SDK 设备组上下文",
                    details={"device_id": device_id},
                )
            resolved_session_id = str(connection.session_id or "").strip()
            if not resolved_session_id:
                raise build_error(
                    ErrorCode.INVALID_MESSAGE,
                    "目标设备当前没有可复用的会话编号",
                    details={"device_id": device_id},
                )

        return self._device_group_runtime.create_context(
            device_id=device_id,
            session_id=resolved_session_id,
            task_id=task_id,
        )

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
            connection_count = len(self._connections)
        log_info(
            self._logger,
            "控制连接已建立",
            LogContext(
                message_id=connection.connection_id,
                fields={
                    "peer": peer,
                    "connection_id": connection.connection_id,
                    "connection_count": connection_count,
                    "heartbeat_interval_ms": self._settings.heartbeat_interval_ms,
                    "heartbeat_timeout_ms": self._settings.heartbeat_timeout_ms,
                },
            ),
        )
        return connection

    def on_transport_closed(self, connection: ControlConnection) -> None:
        """处理底层连接关闭。"""

        removed_device_id: str | None = None
        removed_bindings: tuple[str | None, str | None] = (None, None)
        with self._lock:
            connection.closed = True
            self._connections.pop(connection.connection_id, None)
            connection_count = len(self._connections)
            if connection.device_id:
                current = self._device_connections.get(connection.device_id)
                if current is connection:
                    self._device_connections.pop(connection.device_id, None)
                    removed_device_id = connection.device_id
                    removed_bindings = self._unbind_device_locked(connection.device_id)
                    self._device_group_runtime.mark_device_offline(connection.device_id)
        log_info(
            self._logger,
            "控制连接已关闭并清理运行态",
            self._build_connection_log_context(
                connection,
                fields={
                    "connection_count": connection_count,
                    "removed_device_id": removed_device_id,
                    "removed_binding_glass_device_id": removed_bindings[0],
                    "removed_binding_phone_device_id": removed_bindings[1],
                },
            ),
        )
        self._voice_runtime.on_control_connection_closed(removed_device_id)
        self._notify_binding_removed(removed_bindings)

    def close_connection(self, connection: ControlConnection, *, code: int, reason: str) -> None:
        """关闭一条控制连接。"""

        with self._lock:
            if connection.closed:
                return
            connection.closed = True

        log_warning(
            self._logger,
            "准备关闭控制连接",
            self._build_connection_log_context(
                connection,
                fields={
                    "close_code": code,
                    "close_reason": reason,
                },
            ),
        )
        try:
            connection.close_transport(code, reason)
        finally:
            self.on_transport_closed(connection)

    def _build_connection_log_context(
        self,
        connection: ControlConnection,
        *,
        fields: dict[str, object] | None = None,
    ) -> LogContext:
        """构造控制连接日志上下文。"""

        now = time.monotonic()
        payload: dict[str, object] = {
            "connection_id": connection.connection_id,
            "peer": connection.peer,
            "device_type": connection.device_type,
            "registered": connection.registered,
            "closed": connection.closed,
            "voice_opened": connection.voice_opened,
            "last_seen_age_ms": int((now - connection.last_seen_monotonic) * 1000),
            "heartbeat_age_ms": int((now - connection.last_heartbeat_monotonic) * 1000),
            "heartbeat_timeout_ms": self._settings.heartbeat_timeout_ms,
            "camera_sink_ws_uri": connection.camera_sink_ws_uri,
            "desired_glass_device_id": connection.desired_glass_device_id,
            "desired_phone_device_id": connection.desired_phone_device_id,
            "account_id": connection.account_id,
            "user_id": connection.user_id,
        }
        if fields:
            payload.update(fields)
        return LogContext(
            device_id=connection.device_id,
            session_id=connection.session_id,
            message_id=connection.connection_id,
            fields=payload,
        )

    def handle_text(self, connection: ControlConnection, text: str) -> None:
        """处理一条文本控制消息。"""

        message = self._codec.decode(text)
        context = LogContext(
            trace_id=message.trace_id,
            session_id=message.session_id,
            device_id=connection.device_id,
            message_id=message.message_id,
            fields={
                "connection_id": connection.connection_id,
                "peer": connection.peer,
                "message_name": message.name,
                "semantic": message.semantic,
                "payload_keys": sorted(str(key) for key in message.payload.keys()),
            },
        )
        if self._should_log_control_message(message.name):
            log_debug(self._logger, f"收到控制消息: {message.name}", context)
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
        if message.name == "device.bind":
            self._handle_device_bind(connection, message)
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
        if message.name == "actuator.audio.state":
            self._handle_actuator_audio_state(connection, message)
            return
        if message.name == "actuator.audio.finished":
            self._handle_actuator_audio_finished(connection, message)
            return
        if message.name == "sensor.camera.captured":
            self._handle_camera_captured(connection, message)
            return

        log_debug(self._logger, f"忽略未支持控制消息: {message.name}", context)

    def capture_photo(
        self,
        *,
        device_id: str,
        session_id: str,
        reason: str,
        timeout_ms: int,
    ) -> CameraCaptureResult:
        """通过控制面向设备发起一次抓拍，并等待图片回传。"""

        request_id = f"capture_{uuid.uuid4().hex[:12]}"
        pending = PendingCameraCapture(
            request_id=request_id,
            device_id=device_id,
            session_id=session_id,
            reason=reason,
        )
        with self._lock:
            self._pending_camera_captures[request_id] = pending

        try:
            log_debug(
                self._logger,
                f"camera.capture.request request_id={request_id} reason={reason}",
                LogContext(device_id=device_id, session_id=session_id, message_id=request_id),
            )
            self._send_message_to_device(
                device_id,
                "request",
                "sensor.camera.capture",
                session_id,
                {
                    "device_id": device_id,
                    "request_id": request_id,
                    "reason": reason,
                },
            )
            if not pending.event.wait(timeout_ms / 1000):
                raise build_error(
                    ErrorCode.TIMEOUT,
                    "等待设备抓拍回传超时",
                    details={
                        "device_id": device_id,
                        "session_id": session_id,
                        "request_id": request_id,
                        "timeout_ms": timeout_ms,
                    },
                )
            if pending.error is not None:
                raise pending.error
            if pending.result is None:
                raise build_error(
                    ErrorCode.INTERNAL_ERROR,
                    "抓拍请求已结束，但未收到图片结果",
                    details={"request_id": request_id},
                )
            return pending.result
        finally:
            with self._lock:
                self._pending_camera_captures.pop(request_id, None)

    def start_phone_video_link_debug(
        self,
        *,
        glass_device_id: str,
        target_ws_uri: str,
        frame_interval_ms: int,
        reason: str,
    ):
        """通过调试入口启动一条眼镜到手机的视频直连任务。

        主要逻辑：
        1. 校验眼镜设备当前在线。
        2. 复用当前控制连接上的会话编号。
        3. 直接调用 `backend-task-core` 创建 `phone_video_link_task`。

        参数：
        1. `glass_device_id`：目标眼镜设备编号。
        2. `target_ws_uri`：手机当前显示的接收地址。
        3. `frame_interval_ms`：推帧间隔，单位毫秒。
        4. `reason`：调试原因说明。

        返回值：
        1. 新创建的任务运行态对象。

        异常情况：
        1. 眼镜离线、未开会话或地址为空时抛出结构化错误。
        """

        if frame_interval_ms <= 0:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "frame_interval_ms 必须大于 0",
                details={"frame_interval_ms": frame_interval_ms},
            )

        with self._lock:
            connection = self._device_connections.get(glass_device_id)
        if connection is None or connection.closed:
            raise build_error(
                ErrorCode.STREAM_NOT_FOUND,
                "目标眼镜当前不在线，无法启动视频直连任务",
                details={"glass_device_id": glass_device_id},
            )

        session_id = str(connection.session_id or "").strip()
        if not session_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "目标眼镜尚未打开语音会话，无法复用会话编号启动视频直连任务",
                details={"glass_device_id": glass_device_id},
            )

        resolved_phone_device_id, resolved_target_ws_uri = self._resolve_phone_video_target(
            glass_device_id=glass_device_id,
            target_ws_uri=target_ws_uri.strip(),
        )

        task_gateway = self._voice_runtime.agent_facade.get_task_gateway()
        runtime = task_gateway.create_task(
            task_type="phone_video_link_task",
            session_id=session_id,
            device_id=glass_device_id,
            input_data={
                "phone_device_id": resolved_phone_device_id,
                "target_ws_uri": resolved_target_ws_uri,
                "link_mode": "direct",
                "reason": reason,
                "frame_interval_ms": frame_interval_ms,
            },
        )
        with self._lock:
            self._active_phone_video_task_ids_by_glass[glass_device_id] = runtime.task_id
        return runtime

    def stop_phone_video_link_debug(
        self,
        *,
        glass_device_id: str,
    ):
        """通过调试入口停止一条眼镜到手机的视频直连任务。

        参数：
        1. `glass_device_id`：目标眼镜设备编号。

        返回值：
        1. 被取消后的任务运行态对象。

        异常情况：
        1. 当前眼镜没有活动视频任务时抛出结构化错误。
        """

        with self._lock:
            task_id = self._active_phone_video_task_ids_by_glass.get(glass_device_id)
            connection = self._device_connections.get(glass_device_id)

        if task_id:
            runtime = self._voice_runtime.agent_facade.get_task_gateway().cancel_task(task_id)
            with self._lock:
                self._active_phone_video_task_ids_by_glass.pop(glass_device_id, None)
            return {
                "task_id": runtime.task_id,
                "task_type": runtime.task_type,
                "state": runtime.state,
                "device_id": runtime.device_id,
                "session_id": runtime.session_id,
                "noop": False,
            }

        if connection is None or connection.closed or not connection.registered:
            return {
                "task_id": "",
                "task_type": "phone_video_link_task",
                "state": "cancelled",
                "device_id": glass_device_id,
                "session_id": "",
                "noop": True,
            }

        self._send_message_to_device(
            glass_device_id,
            "request",
            "sensor.camera.stream.stop",
            connection.session_id or "",
            {},
        )
        return {
            "task_id": "",
            "task_type": "phone_video_link_task",
            "state": "cancelled",
            "device_id": glass_device_id,
            "session_id": connection.session_id or "",
            "noop": True,
        }

    def report_task_event(
        self,
        *,
        task_id: str,
        phone_device_id: str,
        event_name: str,
        payload: dict[str, Any] | None = None,
    ):
        """接收手机端通用任务事件并转发到任务网关。"""

        task_gateway = self._voice_runtime.agent_facade.get_task_gateway()
        runtime = task_gateway.query_task(task_id)
        expected_phone_device_id = str(runtime.input.get("phone_device_id") or "").strip()
        if expected_phone_device_id and expected_phone_device_id != phone_device_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "任务事件上报手机与任务绑定手机不一致",
                details={
                    "task_id": task_id,
                    "expected_phone_device_id": expected_phone_device_id,
                    "actual_phone_device_id": phone_device_id,
                },
            )
        if not hasattr(task_gateway, "dispatch_event"):
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "当前任务网关不支持通用事件上报",
                details={"task_id": task_id, "event_name": event_name},
            )
        return task_gateway.dispatch_event(
            task_id=task_id,
            event_name=event_name,
            payload=dict(payload or {}),
            source="phone",
        )

    def _send_device_group_command(
        self,
        *,
        group_id: str,
        role: str,
        device_id: str,
        session_id: str,
        name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """供 SDK 设备组运行时复用的通用控制消息发送适配器。"""

        self._send_message_to_device(
            device_id,
            "request",
            name,
            session_id,
            payload,
        )
        return {
            "ok": True,
            "group_id": group_id,
            "role": role,
            "device_id": device_id,
            "session_id": session_id,
            "name": name,
            "payload": dict(payload),
        }

    def _resolve_phone_video_target(
        self,
        *,
        glass_device_id: str,
        target_ws_uri: str,
    ) -> tuple[str, str]:
        """解析视频直连任务的目标手机与接收地址。

        主要逻辑：
        1. 若显式传入 `target_ws_uri`，则直接使用手动调试目标。
        2. 若未传入，则尝试从当前绑定关系和手机注册信息中解析。
        """

        if target_ws_uri:
            return "manual-debug-phone", target_ws_uri

        with self._lock:
            phone_device_id = self._glass_to_phone.get(glass_device_id)
            phone_connection = self._device_connections.get(phone_device_id or "")
        if not phone_device_id or phone_connection is None or phone_connection.closed:
            raise build_error(
                ErrorCode.STREAM_NOT_FOUND,
                "当前眼镜尚未绑定在线手机，无法自动解析视频接收地址",
                details={"glass_device_id": glass_device_id},
            )
        resolved_target_ws_uri = str(phone_connection.camera_sink_ws_uri or "").strip()
        if not resolved_target_ws_uri:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "已绑定手机尚未上报视频接收地址，无法启动视频直连任务",
                details={"glass_device_id": glass_device_id, "phone_device_id": phone_device_id},
            )
        return phone_device_id, resolved_target_ws_uri

    def build_runtime_snapshot(self) -> dict[str, object]:
        """返回当前运行态快照。"""

        with self._lock:
            connections = list(self._connections.values())
            pending_camera_capture_count = len(self._pending_camera_captures)
            glass_to_phone = dict(self._glass_to_phone)
            phone_to_glass = dict(self._phone_to_glass)

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
                    "device_type": connection.device_type,
                    "peer": connection.peer,
                    "registered": connection.registered,
                    "session_id": connection.session_id,
                    "voice_opened": connection.voice_opened,
                    "camera_sink_ws_uri": connection.camera_sink_ws_uri,
                    "desired_glass_device_id": connection.desired_glass_device_id,
                    "desired_phone_device_id": connection.desired_phone_device_id,
                    "account_id": connection.account_id,
                    "user_id": connection.user_id,
                    "mock_capabilities": dict(connection.mock_capabilities),
                    "last_seen_ms_ago": int((now - connection.last_seen_monotonic) * 1000),
                    "heartbeat_age_ms": int((now - connection.last_heartbeat_monotonic) * 1000),
                }
            )
        snapshot = {
            "online_device_count": len(online_devices),
            "online_devices": online_devices,
            "connections": connection_items,
            "device_bindings": {
                "glass_to_phone": glass_to_phone,
                "phone_to_glass": phone_to_glass,
            },
            "device_groups": self._device_group_runtime.build_snapshot(),
            "pending_camera_capture_count": pending_camera_capture_count,
            "voice_sessions": self._voice_runtime.build_runtime_snapshot(),
        }
        snapshot["diagnostics"] = self._build_runtime_diagnostics(snapshot)
        return snapshot

    def is_device_registered(self, device_id: str) -> bool:
        """判断设备是否已经完成控制面注册。

        功能：
        1. 给音频、视频等旁路连接提供轻量级检查入口。
        2. 区分“只连上音频 WebSocket”和“设备真正进入 SDK 设备组”的状态。

        参数：
        1. `device_id`：设备编号。

        返回值：
        1. 已注册且连接未关闭时返回 True，否则返回 False。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        with self._lock:
            connection = self._device_connections.get(device_id)
            return bool(connection and connection.registered and not connection.closed)

    def _build_runtime_diagnostics(self, snapshot: dict[str, object]) -> dict[str, object]:
        """构建运行态诊断信息。

        主要逻辑：
        1. 找出只有音频连接、但没有控制面注册的设备。
        2. 找出手机或眼镜声明的期望绑定对象是否缺失。
        3. 输出未绑定的在线设备，方便真机联调时快速定位绑定问题。

        参数：
        1. `snapshot`：当前运行态快照。

        返回值：
        1. 面向调试接口展示的结构化诊断信息。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        online_devices = set(str(item) for item in snapshot.get("online_devices", []))
        voice_sessions = snapshot.get("voice_sessions", {})
        voice_device_ids = set(str(item) for item in voice_sessions.keys()) if isinstance(voice_sessions, dict) else set()
        audio_only_device_ids = sorted(voice_device_ids - online_devices)

        connections = snapshot.get("connections", [])
        connection_items = connections if isinstance(connections, list) else []
        desired_binding_issues: list[dict[str, object]] = []
        unbound_online_devices: list[str] = []

        device_bindings = snapshot.get("device_bindings", {})
        glass_to_phone: dict[str, object] = {}
        phone_to_glass: dict[str, object] = {}
        if isinstance(device_bindings, dict):
            raw_glass_to_phone = device_bindings.get("glass_to_phone", {})
            raw_phone_to_glass = device_bindings.get("phone_to_glass", {})
            if isinstance(raw_glass_to_phone, dict):
                glass_to_phone = raw_glass_to_phone
            if isinstance(raw_phone_to_glass, dict):
                phone_to_glass = raw_phone_to_glass

        for item in connection_items:
            if not isinstance(item, dict):
                continue
            if not item.get("registered"):
                continue
            device_id = str(item.get("device_id") or "")
            device_type = str(item.get("device_type") or "")
            if not device_id:
                continue
            if device_type == "phone":
                desired_glass_device_id = str(item.get("desired_glass_device_id") or "")
                if desired_glass_device_id and desired_glass_device_id not in online_devices:
                    desired_binding_issues.append(
                        {
                            "device_id": device_id,
                            "device_type": device_type,
                            "desired_device_id": desired_glass_device_id,
                            "reason": "期望绑定的眼镜尚未完成控制面注册",
                        }
                    )
                if device_id not in phone_to_glass:
                    unbound_online_devices.append(device_id)
            elif device_type == "glass":
                desired_phone_device_id = str(item.get("desired_phone_device_id") or "")
                if desired_phone_device_id and desired_phone_device_id not in online_devices:
                    desired_binding_issues.append(
                        {
                            "device_id": device_id,
                            "device_type": device_type,
                            "desired_device_id": desired_phone_device_id,
                            "reason": "期望绑定的手机尚未完成控制面注册",
                        }
                    )
                if device_id not in glass_to_phone:
                    unbound_online_devices.append(device_id)

        return {
            "audio_only_device_ids": audio_only_device_ids,
            "desired_binding_issues": desired_binding_issues,
            "unbound_online_devices": sorted(set(unbound_online_devices)),
        }

    def _handle_register(self, connection: ControlConnection, message: ControlMessage) -> None:
        payload = message.payload
        device_id = str(payload.get("device_id", "")).strip()
        device_type = str(payload.get("device_type", "glass")).strip() or "glass"
        auth = payload.get("auth", {})
        auth_mode = str(auth.get("mode", "")).strip() if isinstance(auth, dict) else ""
        pair_token = str(auth.get("pair_token", "")).strip() if isinstance(auth, dict) else ""
        camera_sink_ws_uri = str(payload.get("camera_sink_ws_uri", "")).strip() or None
        desired_glass_device_id = str(payload.get("desired_glass_device_id", "")).strip() or None
        desired_phone_device_id = str(payload.get("desired_phone_device_id", "")).strip() or None
        account_id = str(payload.get("account_id", "")).strip() or None
        user_id = str(payload.get("user_id", "")).strip() or None
        mock_capabilities = payload.get("mock_capabilities")
        if not isinstance(mock_capabilities, dict):
            mock_capabilities = {}

        if not device_id:
            self._send_register_failed(
                connection=connection,
                device_id="",
                device_type=device_type,
                reason="device_id 不能为空",
                code=ErrorCode.INVALID_MESSAGE,
            )
            return
        if auth_mode != "pair_token":
            self._send_register_failed(
                connection=connection,
                device_id=device_id,
                device_type=device_type,
                reason="仅支持 mode=pair_token",
                code=ErrorCode.UNAUTHORIZED,
            )
            return

        expected = self._settings.parse_device_token_map().get(device_id)
        if not expected or expected != pair_token:
            self._send_register_failed(
                connection=connection,
                device_id=device_id,
                device_type=device_type,
                reason="pair_token 校验失败",
                code=ErrorCode.UNAUTHORIZED,
            )
            return

        old_connection: ControlConnection | None = None
        should_open_voice_session = device_type == "glass"
        with self._lock:
            current = self._device_connections.get(device_id)
            if current and current is not connection:
                old_connection = current
            connection.device_id = device_id
            connection.device_type = device_type
            connection.registered = True
            connection.voice_opened = False
            connection.session_id = f"sess_{uuid.uuid4().hex[:12]}" if should_open_voice_session else None
            connection.camera_sink_ws_uri = camera_sink_ws_uri
            connection.desired_glass_device_id = desired_glass_device_id
            connection.desired_phone_device_id = desired_phone_device_id
            connection.account_id = account_id
            connection.user_id = user_id
            connection.mock_capabilities = dict(mock_capabilities)
            connection.touch_heartbeat()
            self._device_connections[device_id] = connection
            self._device_group_runtime.register_device(
                device_id=device_id,
                role=self._normalize_device_group_role(device_type),
                account_id=account_id,
                user_id=user_id,
                capabilities=set(str(item) for item in mock_capabilities.keys()),
                metadata={
                    "device_type": device_type,
                    "peer": connection.peer,
                    "camera_sink_ws_uri": camera_sink_ws_uri,
                    "desired_glass_device_id": desired_glass_device_id,
                    "desired_phone_device_id": desired_phone_device_id,
                    "account_id": account_id,
                    "user_id": user_id,
                },
            )
            if should_open_voice_session and connection.session_id is not None:
                self._voice_runtime.open_session(
                    device_id=device_id,
                    device_type=device_type,
                    session_id=connection.session_id,
                )

        if old_connection is not None:
            log_warning(
                self._logger,
                f"检测到同设备重连，关闭旧连接: device_id={device_id}",
                self._build_connection_log_context(
                    old_connection,
                    fields={
                        "new_connection_id": connection.connection_id,
                    },
                ),
            )
            self.close_connection(old_connection, code=4001, reason="replaced by new connection")

        log_info(
            self._logger,
            "设备注册成功",
            self._build_connection_log_context(
                connection,
                fields={
                    "auth_mode": auth_mode,
                    "mock_capabilities": sorted(str(item) for item in mock_capabilities.keys()),
                    "should_open_voice_session": should_open_voice_session,
                },
            ),
        )

        self._send_message(
            connection,
            create_control_message(
                semantic="notify",
                name="device.registered",
                source=self._server_endpoint(),
                target=self._device_endpoint(device_id, device_type),
                payload={
                    "device_id": device_id,
                    "account_id": account_id,
                    "user_id": user_id,
                    "heartbeat_interval_ms": self._settings.heartbeat_interval_ms,
                },
                trace_id=message.trace_id,
            ),
        )
        if should_open_voice_session and connection.session_id is not None:
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
        self._try_auto_bind_after_register(device_id=device_id)

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
        age_ms = int((time.monotonic() - connection.last_heartbeat_monotonic) * 1000)
        connection.touch_heartbeat()
        if age_ms > self._settings.heartbeat_interval_ms * 2:
            log_debug(
                self._logger,
                "收到延迟心跳",
                self._build_connection_log_context(
                    connection,
                    fields={
                        "heartbeat_age_before_touch_ms": age_ms,
                        "heartbeat_interval_ms": self._settings.heartbeat_interval_ms,
                    },
                ),
            )

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

    def _handle_device_bind(self, connection: ControlConnection, message: ControlMessage) -> None:
        """处理设备绑定请求。"""

        glass_device_id = str(message.payload.get("glass_device_id", "")).strip()
        phone_device_id = str(message.payload.get("phone_device_id", "")).strip()
        if not glass_device_id or not phone_device_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "device.bind 需要同时提供 glass_device_id 和 phone_device_id",
                details={"payload": message.payload},
            )

        self._bind_devices(glass_device_id=glass_device_id, phone_device_id=phone_device_id)

    def _try_auto_bind_after_register(self, *, device_id: str) -> None:
        """在设备注册成功后尝试自动完成手机与眼镜配对。"""

        with self._lock:
            connection = self._device_connections.get(device_id)
            if connection is None or connection.closed or not connection.registered:
                return

            candidate_pair: tuple[str, str] | None = None
            if connection.device_type == "phone" and connection.desired_glass_device_id:
                glass_connection = self._device_connections.get(connection.desired_glass_device_id)
                if glass_connection and not glass_connection.closed and glass_connection.registered:
                    candidate_pair = (connection.desired_glass_device_id, connection.device_id or "")
            elif connection.device_type == "glass":
                if connection.desired_phone_device_id:
                    phone_connection = self._device_connections.get(connection.desired_phone_device_id)
                    if phone_connection and not phone_connection.closed and phone_connection.registered:
                        candidate_pair = (connection.device_id or "", connection.desired_phone_device_id)
                else:
                    for phone_connection in self._device_connections.values():
                        if phone_connection.closed or not phone_connection.registered:
                            continue
                        if phone_connection.device_type != "phone":
                            continue
                        if phone_connection.desired_glass_device_id == connection.device_id:
                            candidate_pair = (connection.device_id or "", phone_connection.device_id or "")
                            break
            if candidate_pair is None:
                online_glass_ids = [
                    item.device_id or ""
                    for item in self._device_connections.values()
                    if item.registered and not item.closed and item.device_type == "glass" and item.device_id
                ]
                online_phone_ids = [
                    item.device_id or ""
                    for item in self._device_connections.values()
                    if item.registered and not item.closed and item.device_type == "phone" and item.device_id
                ]
                if (
                    len(online_glass_ids) == 1
                    and len(online_phone_ids) == 1
                    and online_glass_ids[0] not in self._glass_to_phone
                    and online_phone_ids[0] not in self._phone_to_glass
                ):
                    glass_connection = self._device_connections.get(online_glass_ids[0])
                    phone_connection = self._device_connections.get(online_phone_ids[0])
                    if self._connections_share_account(glass_connection, phone_connection):
                        candidate_pair = (online_glass_ids[0], online_phone_ids[0])

        if candidate_pair is None:
            return
        glass_device_id, phone_device_id = candidate_pair
        try:
            self._bind_devices(glass_device_id=glass_device_id, phone_device_id=phone_device_id)
            log_info(
                self._logger,
                "设备已自动绑定",
                LogContext(
                    device_id=glass_device_id,
                    fields={
                        "glass_device_id": glass_device_id,
                        "phone_device_id": phone_device_id,
                    },
                ),
            )
        except Exception as exc:
            log_warning(
                self._logger,
                "自动绑定失败，已等待后续重试",
                LogContext(
                    device_id=glass_device_id,
                    fields={
                        "glass_device_id": glass_device_id,
                        "phone_device_id": phone_device_id,
                        "reason": str(exc),
                    },
                ),
            )

    def _bind_devices(self, *, glass_device_id: str, phone_device_id: str) -> None:
        """绑定一台眼镜和一台手机，并向双方发送绑定完成通知。"""

        with self._lock:
            glass_connection = self._device_connections.get(glass_device_id)
            phone_connection = self._device_connections.get(phone_device_id)
            if glass_connection is None or glass_connection.closed or not glass_connection.registered:
                raise build_error(
                    ErrorCode.STREAM_NOT_FOUND,
                    "目标眼镜当前不在线",
                    details={"glass_device_id": glass_device_id},
                )
            if phone_connection is None or phone_connection.closed or not phone_connection.registered:
                raise build_error(
                    ErrorCode.STREAM_NOT_FOUND,
                    "目标手机当前不在线",
                    details={"phone_device_id": phone_device_id},
                )
            if glass_connection.device_type != "glass":
                raise build_error(
                    ErrorCode.INVALID_MESSAGE,
                    "glass_device_id 对应设备类型不是 glass",
                    details={"glass_device_id": glass_device_id, "device_type": glass_connection.device_type},
                )
            if phone_connection.device_type != "phone":
                raise build_error(
                    ErrorCode.INVALID_MESSAGE,
                    "phone_device_id 对应设备类型不是 phone",
                    details={"phone_device_id": phone_device_id, "device_type": phone_connection.device_type},
                )

            bound_phone_id = self._glass_to_phone.get(glass_device_id)
            bound_glass_id = self._phone_to_glass.get(phone_device_id)
            if bound_phone_id == phone_device_id and bound_glass_id == glass_device_id:
                return
            if bound_phone_id is not None and bound_phone_id != phone_device_id:
                raise build_error(
                    ErrorCode.INVALID_MESSAGE,
                    "目标眼镜已绑定其它手机",
                    details={"glass_device_id": glass_device_id, "bound_phone_id": bound_phone_id},
                )
            if bound_glass_id is not None and bound_glass_id != glass_device_id:
                raise build_error(
                    ErrorCode.INVALID_MESSAGE,
                    "目标手机已绑定其它眼镜",
                    details={"phone_device_id": phone_device_id, "bound_glass_id": bound_glass_id},
                )

            self._device_group_runtime.bind_devices(
                glass_device_id=glass_device_id,
                phone_device_id=phone_device_id,
            )
            self._glass_to_phone[glass_device_id] = phone_device_id
            self._phone_to_glass[phone_device_id] = glass_device_id

        bind_payload = {
            "glass_device_id": glass_device_id,
            "phone_device_id": phone_device_id,
        }
        self._send_message_to_device(glass_device_id, "notify", "device.binded", "", bind_payload)
        self._send_message_to_device(phone_device_id, "notify", "device.binded", "", bind_payload)
        log_info(
            self._logger,
            "设备绑定完成",
            LogContext(
                device_id=glass_device_id,
                fields={
                    "glass_device_id": glass_device_id,
                    "phone_device_id": phone_device_id,
                },
            ),
        )

    @staticmethod
    def _connections_share_account(
        glass_connection: ControlConnection | None,
        phone_connection: ControlConnection | None,
    ) -> bool:
        """判断两条连接是否允许按账号维度自动绑定。

        主要逻辑：
        1. 双方都声明 `account_id` 时必须一致。
        2. 任一方未声明账号时保持向前兼容，允许单眼镜单手机兜底绑定。
        """

        if glass_connection is None or phone_connection is None:
            return False
        glass_account_id = str(glass_connection.account_id or "").strip()
        phone_account_id = str(phone_connection.account_id or "").strip()
        return not glass_account_id or not phone_account_id or glass_account_id == phone_account_id

    @staticmethod
    def _normalize_device_group_role(device_type: str) -> str:
        """归一化 SDK 设备组角色。

        功能：
        1. 将控制面设备类型映射到 SDK 设备组角色。
        2. 对未知类型保守归为 `server`，避免污染 glass / phone 角色。

        参数：
        1. `device_type`：控制面设备类型。

        返回值：
        1. SDK 设备组角色。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        if device_type in {"glass", "phone", "server"}:
            return device_type
        return "server"

    def _handle_segment_started(self, connection: ControlConnection, message: ControlMessage) -> None:
        connection.last_seen_monotonic = time.monotonic()
        segment_id = str(message.payload.get("segment_id", "")).strip()
        stream_id = str(message.payload.get("stream_id", "")).strip()
        self._voice_runtime.on_segment_started(
            device_id=connection.device_id or "",
            session_id=message.session_id or "",
            payload=message.payload,
        )
        log_debug(
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
        connection.last_seen_monotonic = time.monotonic()
        stream_id = str(message.payload.get("stream_id") or message.stream_id or "").strip()
        self._voice_runtime.on_playback_started(
            device_id=connection.device_id or "",
            session_id=message.session_id or "",
            stream_id=stream_id,
        )

    def _handle_actuator_audio_finished(self, connection: ControlConnection, message: ControlMessage) -> None:
        connection.last_seen_monotonic = time.monotonic()
        stream_id = str(message.payload.get("stream_id") or message.stream_id or "").strip()
        self._voice_runtime.on_playback_finished(
            device_id=connection.device_id or "",
            session_id=message.session_id or "",
            stream_id=stream_id,
        )

    def _handle_actuator_audio_state(self, connection: ControlConnection, message: ControlMessage) -> None:
        """处理设备回传的结构化播放状态。"""

        connection.last_seen_monotonic = time.monotonic()
        stream_id = str(message.payload.get("stream_id") or message.stream_id or "").strip()
        state = str(message.payload.get("state", "")).strip()
        reason = str(message.payload.get("reason", "")).strip() or None
        if not stream_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "actuator.audio.state 缺少 stream_id",
            )
        if not state:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "actuator.audio.state 缺少 state",
            )
        self._voice_runtime.on_playback_state(
            device_id=connection.device_id or "",
            session_id=message.session_id or "",
            stream_id=stream_id,
            state=state,
            reason=reason,
        )

    def _handle_camera_captured(self, connection: ControlConnection, message: ControlMessage) -> None:
        """处理设备回传的单次抓拍结果。"""

        request_id = str(message.payload.get("request_id", "")).strip()
        if not request_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "sensor.camera.captured 缺少 request_id",
            )

        with self._lock:
            pending = self._pending_camera_captures.get(request_id)
        if pending is None:
            log_warning(
                self._logger,
                f"收到未知抓拍回传，已忽略: request_id={request_id}",
                LogContext(
                    trace_id=message.trace_id,
                    session_id=message.session_id,
                    device_id=connection.device_id,
                    message_id=message.message_id,
                ),
            )
            return

        if connection.device_id and pending.device_id != connection.device_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "抓拍回传的设备与请求设备不一致",
                details={
                    "request_device_id": pending.device_id,
                    "connection_device_id": connection.device_id,
                    "request_id": request_id,
                },
            )

        if message.session_id and pending.session_id != message.session_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "抓拍回传的 session_id 与请求不一致",
                details={
                    "request_session_id": pending.session_id,
                    "actual_session_id": message.session_id,
                    "request_id": request_id,
                },
            )

        ok = bool(message.payload.get("ok", True))
        if not ok:
            error_payload = message.payload.get("error", {})
            error_message = "设备抓拍失败"
            if isinstance(error_payload, dict):
                error_message = str(error_payload.get("message") or error_message)
            pending.error = build_error(
                ErrorCode.INTERNAL_ERROR,
                error_message,
                details={
                    "request_id": request_id,
                    "error": error_payload,
                },
            )
            pending.event.set()
            return

        image_base64 = str(message.payload.get("image_base64", "")).strip()
        if not image_base64:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "sensor.camera.captured 缺少 image_base64",
                details={"request_id": request_id},
            )
        try:
            image_bytes = base64.b64decode(image_base64)
        except Exception as exc:  # pragma: no cover - 非法输入防御
            raise build_error(
                ErrorCode.DECODE_ERROR,
                "image_base64 解码失败",
                details={"request_id": request_id, "reason": str(exc)},
            ) from exc

        mime_type = str(message.payload.get("mime_type", "image/jpeg")).strip() or "image/jpeg"
        codec = str(message.payload.get("codec", "jpeg")).strip() or "jpeg"
        width = message.payload.get("width")
        height = message.payload.get("height")
        pending.result = CameraCaptureResult(
            request_id=request_id,
            image_bytes=image_bytes,
            mime_type=mime_type,
            codec=codec,
            width=int(width) if isinstance(width, (int, float)) else None,
            height=int(height) if isinstance(height, (int, float)) else None,
            meta={
                "reason": pending.reason,
                "image_bytes": len(image_bytes),
            },
        )
        pending.event.set()
        log_debug(
            self._logger,
            f"camera.capture.result request_id={request_id} mime_type={mime_type} bytes={len(image_bytes)}",
            LogContext(
                trace_id=message.trace_id,
                session_id=message.session_id,
                device_id=connection.device_id,
                message_id=message.message_id,
            ),
        )

    def _send_register_failed(
        self,
        *,
        connection: ControlConnection,
        device_id: str,
        device_type: str,
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
                target=self._device_endpoint(target_device_id, device_type),
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
            log_debug(
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
                log_warning(
                    self._logger,
                    "设备心跳超时，关闭连接",
                    self._build_connection_log_context(
                        connection,
                        fields={
                            "close_code": 4000,
                            "close_reason": "heartbeat timeout",
                        },
                    ),
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
            module="phone-api" if device_type == "phone" else "glass-api",
        )

    def _unbind_device_locked(self, device_id: str) -> tuple[str | None, str | None]:
        """在持锁状态下清理与目标设备相关的绑定关系。"""

        if device_id in self._glass_to_phone:
            phone_device_id = self._glass_to_phone.pop(device_id)
            self._phone_to_glass.pop(phone_device_id, None)
            return device_id, phone_device_id
        if device_id in self._phone_to_glass:
            glass_device_id = self._phone_to_glass.pop(device_id)
            self._glass_to_phone.pop(glass_device_id, None)
            return glass_device_id, device_id
        return None, None

    def _notify_binding_removed(self, binding: tuple[str | None, str | None]) -> None:
        """在绑定关系被移除后打印调试日志。"""

        glass_device_id, phone_device_id = binding
        if not glass_device_id or not phone_device_id:
            return
        log_info(
            self._logger,
            "设备绑定已移除",
            LogContext(
                device_id=glass_device_id,
                fields={
                    "glass_device_id": glass_device_id,
                    "phone_device_id": phone_device_id,
                },
            ),
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

    def _handle_task_event(self, event: TaskEvent) -> None:
        """根据任务事件驱动设备控制消息。"""

        if event.task_type != "phone_video_link_task":
            return
        if event.event_name == "task.started":
            with self._lock:
                self._active_phone_video_task_ids_by_glass[event.device_id] = event.task_id
            stream_id = str(event.payload.get("stream_id", "")).strip()
            target_ws_uri = str(event.payload.get("target_ws_uri", "")).strip()
            if not stream_id or not target_ws_uri:
                return
            self._send_message_to_device(
                event.device_id,
                "request",
                "sensor.camera.stream.start",
                event.session_id,
                {
                    "stream_id": stream_id,
                    "target_ws_uri": target_ws_uri,
                    "frame_interval_ms": int(event.payload.get("frame_interval_ms", 500)),
                    "codec": str(event.payload.get("codec", "jpeg")),
                },
            )
            return
        if event.event_name == "task.cancelled":
            with self._lock:
                self._active_phone_video_task_ids_by_glass.pop(event.device_id, None)
            stream_id = str(event.payload.get("stream_id", "")).strip()
            if not stream_id:
                return
            self._send_message_to_device(
                event.device_id,
                "request",
                "sensor.camera.stream.stop",
                event.session_id,
                {
                    "stream_id": stream_id,
                },
            )
            return
        if event.event_name in {"task.completed", "task.failed"}:
            with self._lock:
                self._active_phone_video_task_ids_by_glass.pop(event.device_id, None)
