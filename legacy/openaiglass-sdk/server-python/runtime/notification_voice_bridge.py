"""通知和任务事件到语音播放的桥接层。

本模块把结构化通知、后台 TaskEvent、Agent 回流结果和统一播放流连接起来。
它不实现播放队列本身，而是通过 VoiceRuntime 传入的回调复用同一套播放仲裁、
TTS 合成和设备控制消息入口。
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable

from agent_core import AgentFacade
from agent_core.context import generate_id
from backend_task_core import TaskEvent
from infra.logging import LogContext, log_debug
from runtime.notifications import NotificationCoordinator, NotificationRequest
from runtime.playback_arbiter import PlaybackArbiter
from runtime.voice_state import PlaybackStreamContext, ReplySynthesisContext, VoiceSessionController


class NotificationVoiceBridge:
    """把 SDK 通知和 Task 事件桥接到语音播报链路。

    主要功能：
    1. 将 `submit_notification(...)` 的外部通知转换为 `NotificationRequest`。
    2. 处理 NotificationCoordinator 批准后的直接播报。
    3. 处理高优先级通知对当前通知播放流的中断。
    4. 将需要 Agent 决策的 TaskEvent 转换为 AgentTurn，并把 Agent 回复重新提交通知。

    主要属性：
    1. `notification_coordinator`：统一通知协调器。
    2. `task_event_bridge`：TaskEvent 到会话上下文和 AgentTurn 的转换器。
    3. 多个回调用于复用 VoiceRuntime 的播放、合成和设备控制能力。
    """

    def __init__(
        self,
        *,
        agent_facade: AgentFacade,
        task_event_bridge,
        notification_coordinator: NotificationCoordinator,
        lock,
        controllers: dict[str, VoiceSessionController],
        playback_streams: dict[tuple[str, str], PlaybackStreamContext],
        notification_stream_requests: dict[tuple[str, str], str],
        notification_request_streams: dict[str, tuple[str, str]],
        playback_arbiter: PlaybackArbiter,
        send_control_message: Callable[[str, str, str, str, dict[str, Any]], None],
        open_reply_synthesis_context: Callable[..., ReplySynthesisContext],
        synthesize_text_into_context: Callable[..., None],
        mark_playback_interrupted_locked: Callable[..., None],
        logger,
    ) -> None:
        """初始化通知语音桥接层。

        主要逻辑：
        1. 保存通知协调器、TaskEventBridge 和 AgentFacade。
        2. 保存播放流索引和状态锁引用，保证和 VoiceRuntime 使用同一份状态。
        3. 保存播放创建、文本合成、中断标记和控制消息回调。

        参数：
        1. `agent_facade`：执行 Agent 回流轮次。
        2. `task_event_bridge`：转换和落盘 TaskEvent。
        3. `notification_coordinator`：统一通知协调器。
        4. 其余参数为 VoiceRuntime 的共享状态和回调。

        返回值：
        1. 无返回值。

        异常情况：
        1. 初始化不访问外部系统，不抛出业务异常。
        """

        self._agent_facade = agent_facade
        self._task_event_bridge = task_event_bridge
        self._notification_coordinator = notification_coordinator
        self._lock = lock
        self._controllers = controllers
        self._playback_streams = playback_streams
        self._notification_stream_requests = notification_stream_requests
        self._notification_request_streams = notification_request_streams
        self._playback_arbiter = playback_arbiter
        self._send_control_message = send_control_message
        self._open_reply_synthesis_context = open_reply_synthesis_context
        self._synthesize_text_into_context = synthesize_text_into_context
        self._mark_playback_interrupted_locked = mark_playback_interrupted_locked
        self._logger = logger

    def set_notification_coordinator(self, notification_coordinator: NotificationCoordinator) -> None:
        """更新通知协调器引用。

        主要逻辑：
        1. 迁移期部分单测会替换 `VoiceRuntime._notification_coordinator`。
        2. Bridge 需要同步到新的协调器，避免公开方法仍写入旧对象。

        参数：
        1. `notification_coordinator`：新的通知协调器。

        返回值：
        1. 无返回值。

        异常情况：
        1. 本函数不抛出业务异常。
        """

        self._notification_coordinator = notification_coordinator

    def on_task_event(self, event: TaskEvent) -> None:
        """处理后台任务事件。

        主要逻辑：
        1. 通过 `TaskEventBridge` 把事件写入会话上下文。
        2. 对允许直发的事件，交给统一通知协调器裁决与下发。
        3. 对要求回流决策的事件，再转换成 `AgentTurn` 交给 `agent-core`。

        参数：
        1. `event`：后台任务事件。

        返回值：
        1. 无返回值。

        异常情况：
        1. Agent 回流在后台线程中执行，异常会被记录为 DEBUG。
        """

        request = self._task_event_bridge.handle_event(event)
        if request is None:
            dispatched = False
        else:
            submit_result = self._notification_coordinator.submit(request)
            dispatched = submit_result.dispatched
        if event.requires_agent_decision:
            threading.Thread(
                target=self.run_task_event_agent_turn,
                args=(event, dispatched),
                daemon=True,
            ).start()

    def submit_notification(
        self,
        *,
        request_id: str,
        source_module: str,
        session_id: str,
        device_id: str,
        task_id: str | None,
        text: str,
        priority: str = "normal",
        notification_type: str = "sdk.notification",
        allow_interrupt: bool | None = None,
        allow_merge: bool | None = None,
        requires_agent_context_sync: bool = False,
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        """提交一条外部结构化通知到统一语音播报链路。

        主要逻辑：
        1. 过滤空播报文本。
        2. 根据优先级补齐默认中断和合并策略。
        3. 提交给 `NotificationCoordinator`，由协调器决定立即播报、排队或合并。

        参数：
        1. `request_id`：通知请求编号。
        2. `source_module`：通知来源模块。
        3. `session_id/device_id/task_id`：通知归属上下文。
        4. `text`：需要播报的文本。
        5. `priority`：通知优先级。
        6. 其余参数为通知仲裁策略。

        返回值：
        1. 提交结果字典。

        异常情况：
        1. 空文本不会抛异常，会返回 `accepted=false`。
        """

        resolved_text = text.strip()
        if not resolved_text:
            return {
                "accepted": False,
                "dispatched": False,
                "queued": False,
                "reason": "empty_notification_text",
            }
        resolved_allow_interrupt = priority in {"high", "critical"} if allow_interrupt is None else allow_interrupt
        resolved_allow_merge = priority in {"low", "normal"} if allow_merge is None else allow_merge
        result = self._notification_coordinator.submit(
            NotificationRequest(
                request_id=request_id,
                source_module=source_module,
                session_id=session_id,
                device_id=device_id,
                task_id=task_id,
                priority=priority,
                notification_type=notification_type,
                delivery_mode="audio",
                allow_interrupt=resolved_allow_interrupt,
                allow_merge=resolved_allow_merge,
                requires_agent_context_sync=requires_agent_context_sync,
                dedupe_key=dedupe_key or f"{notification_type}:{task_id or request_id}:{resolved_text}",
                payload={"text": resolved_text},
            )
        )
        return {
            "accepted": result.accepted,
            "dispatched": result.dispatched,
            "queued": result.queued,
            "interrupted_active": result.interrupted_active,
            "reason": result.reason,
            "active_request_id": result.active_request_id,
            "queued_position": result.queued_position,
        }

    def dispatch_notification_request(self, request: NotificationRequest) -> None:
        """把通过裁决的通知申请转成实际播报。

        参数：
        1. `request`：已被协调器批准的通知请求。

        返回值：
        1. 无返回值。

        异常情况：
        1. 播放失败由后台线程记录 DEBUG。
        """

        threading.Thread(
            target=self.play_notification_request,
            args=(request,),
            daemon=True,
        ).start()

    def play_notification_request(self, request: NotificationRequest) -> None:
        """播报通知协调器批准的通知。

        主要逻辑：
        1. 创建统一播放流并记录通知请求到播放流的双向映射。
        2. 下发 `assistant.reply`，让眼镜端知道本次播报文本和 stream_id。
        3. 通过当前 TTS 链路把文本合成进同一个播放流。

        参数：
        1. `request`：通知请求。

        返回值：
        1. 无返回值。

        异常情况：
        1. 真机播放失败会记录 DEBUG，不影响运行时继续处理后续通知。
        """

        text = str(request.payload.get("text", "")).strip()
        if not text:
            return
        try:
            context = self._open_reply_synthesis_context(
                device_id=request.device_id,
                session_id=request.session_id,
                source="vision_alert" if request.notification_type.startswith("vision.") else "task_notification",
                priority=request.priority,
                interrupt_policy=request.interrupt_policy,
                resume_policy=request.resume_policy,
                task_id=request.task_id,
            )
            with self._lock:
                self._notification_stream_requests[(request.device_id, context.stream_id)] = request.request_id
                self._notification_request_streams[request.request_id] = (request.device_id, context.stream_id)
            self._send_control_message(
                request.device_id,
                "notify",
                "assistant.reply",
                request.session_id,
                {
                    "device_id": request.device_id,
                    "text": text,
                    "stream_id": context.stream_id,
                    "task_id": request.task_id,
                    "task_type": request.payload.get("task_type"),
                    "task_state": request.payload.get("task_state"),
                    "priority": request.priority,
                    "interrupt_policy": request.interrupt_policy,
                    "resume_policy": request.resume_policy,
                },
            )
            self._synthesize_text_into_context(
                device_id=request.device_id,
                session_id=request.session_id,
                context=context,
                text=text,
            )
        except Exception as exc:  # pragma: no cover - 真机联调路径
            log_debug(
                self._logger,
                f"通知播报失败，已忽略: reason={exc!r}",
                LogContext(device_id=request.device_id, session_id=request.session_id, message_id=request.request_id),
            )

    def run_task_event_agent_turn(self, event: TaskEvent, dispatched_direct_notify: bool) -> None:
        """执行后台任务事件的 Agent 回流主路径。

        主要逻辑：
        1. 把 TaskEvent 转成来源为 `task_event` 的 AgentTurn。
        2. 执行 Agent 并读取回复文本。
        3. 如果没有直接通知过，则把 Agent 回复重新提交为通知播报。

        参数：
        1. `event`：后台任务事件。
        2. `dispatched_direct_notify`：是否已经直发通知。

        返回值：
        1. 无返回值。

        异常情况：
        1. Agent 回流失败会记录 DEBUG，不影响后续事件处理。
        """

        try:
            turn = self._task_event_bridge.convert_event_to_agent_turn(event)
            agent_result = self._agent_facade.handle_turn(turn)
            reply_text = agent_result.reply_text.strip()
            if not reply_text or dispatched_direct_notify:
                return
            self._notification_coordinator.submit(
                NotificationRequest(
                    request_id=generate_id("notify_req"),
                    source_module="agent-core",
                    session_id=event.session_id,
                    device_id=event.device_id,
                    task_id=event.task_id,
                    priority=event.priority,
                    notification_type=f"{event.event_name}.agent_reply",
                    delivery_mode="audio",
                    allow_interrupt=event.priority in {"high", "critical"},
                    allow_merge=event.priority in {"low", "normal"},
                    requires_agent_context_sync=False,
                    dedupe_key=f"{event.event_name}:{event.task_id}:agent_reply",
                    payload={
                        "text": reply_text,
                        "task_type": event.task_type,
                        "task_state": event.state,
                    },
                )
            )
        except Exception as exc:  # pragma: no cover - 真机联调路径
            log_debug(
                self._logger,
                f"任务事件回流 agent-core 失败，已忽略: reason={exc!r}",
                LogContext(device_id=event.device_id, session_id=event.session_id, message_id=event.event_id),
            )

    def interrupt_notification_request(self, request: NotificationRequest) -> None:
        """中断当前活动的通知播报流。

        主要逻辑：
        1. 根据通知编号找到对应播放流。
        2. 只摘除当前通知对应的播放流，不清空普通回复待播队列。
        3. 先向设备显式下发 `actuator.audio.interrupt`，再让新的高优先级通知接管活动位置。

        参数：
        1. `request`：需要被中断的通知请求。

        返回值：
        1. 无返回值。

        异常情况：
        1. 找不到对应播放流时静默返回。
        """

        playback: PlaybackStreamContext | None = None
        interrupt_device_id: str | None = None
        interrupt_session_id: str | None = None
        interrupt_stream_id: str | None = None
        with self._lock:
            stream_ref = self._notification_request_streams.pop(request.request_id, None)
            if stream_ref is None:
                return
            device_id, stream_id = stream_ref
            interrupt_device_id = device_id
            interrupt_stream_id = stream_id
            self._notification_stream_requests.pop((device_id, stream_id), None)
            playback = self._playback_streams.pop((device_id, stream_id), None)
            self._playback_arbiter.remove(device_id=device_id, stream_id=stream_id)
            controller = self._controllers.get(device_id)
            if playback is None or controller is None:
                return
            interrupt_session_id = playback.session_id
            self._mark_playback_interrupted_locked(
                controller=controller,
                playback=playback,
                reason="higher_priority_notification",
            )
        if (
            interrupt_device_id is not None
            and interrupt_session_id is not None
            and interrupt_stream_id is not None
        ):
            self._send_control_message(
                interrupt_device_id,
                "request",
                "actuator.audio.interrupt",
                interrupt_session_id,
                {
                    "device_id": interrupt_device_id,
                    "stream_id": interrupt_stream_id,
                    "reason": "higher_priority_notification",
                    "request_id": request.request_id,
                    "resume_policy": request.resume_policy,
                },
            )
        try:
            playback.queue.put_nowait(None)
        except queue.Full:
            pass
