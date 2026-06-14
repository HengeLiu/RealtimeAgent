from __future__ import annotations

from typing import Callable

from realtime_agent.conversation.core.vision_host import VisionRealtimeAgentCore
from realtime_agent.conversation.core.base import AgentCoreEvent, AgentSnapshot, ConversationContext, TaskSignal
from realtime_agent.conversation.core.loop import VlAgentLoop
from realtime_agent.conversation.events import ConversationRuntimeEventEmitter
from realtime_agent.conversation.input import CallbackVisualInputBoundary, SileroSpeechInputBoundary
from realtime_agent.conversation.input.asr_session import AsrProviderSessionPool
from realtime_agent.conversation.output.bridge import ConversationOutputDeltaBridge
from realtime_agent.conversation.turn import OutputInterruptionController, RealtimeTurnController
from realtime_agent.conversation.types import SpeechInputDelta
from realtime_agent.observability import RunRecorder
from realtime_agent.protocol import StreamChunk
from realtime_agent.tools import ToolGateway


class VisionConversationRuntime:
    """VL conversation runtime。

    主要功能：以旧 `AgentCore` 兼容接口接入 `RealtimeAgentApp`，内部与 Omni manual
    采用完全一致的 turn 边界判定：同样用 `SileroSpeechInputBoundary` 产生
    `turn_started/turn_ended`，ASR 仅作为按 turn 开闭的转写器交给 `VlAgentLoop`。该
    runtime 复用现有 `VisionRealtimeAgentCore` 的上下文、工具、视觉资产、VLM 和 TTS
    输出逻辑，只替换输入边界和 turn 控制。
    主要属性：`core` 是现有 Vision core；`speech_boundary` 是 Silero 语音边界；
    `loop` 持有按 turn 开闭的 ASR 转写器。
    """

    def __init__(
        self,
        *,
        core: VisionRealtimeAgentCore,
        recorder: RunRecorder,
        speech_boundary: SileroSpeechInputBoundary,
        asr_sessions: AsrProviderSessionPool,
    ) -> None:
        self.core = core
        self.loop = VlAgentLoop(
            core=core,
            stream_id_for_session=lambda session_id: self._upstream_by_session.get(session_id, ""),
            asr_sessions=asr_sessions,
        )
        self.speech_boundary = speech_boundary
        self.emitter = ConversationRuntimeEventEmitter(recorder=recorder)
        self.output_delta_bridge = ConversationOutputDeltaBridge(output_service=core.output_service, recorder=recorder)
        self.output_delta_bridge.bind()
        self.turn_controller = RealtimeTurnController(
            emitter=self.emitter,
            interruption_controller=OutputInterruptionController(
                active_output_stream_id=self._active_output_stream_id,
                state=self._state,
            ),
            stream_id_for_session=lambda session_id: self._upstream_by_session.get(session_id, ""),
            visual_boundary=CallbackVisualInputBoundary(
                on_started=self._sync_conversation_speech_started,
                on_ended=self._sync_conversation_speech_stopped,
            ),
        )
        self._session_by_user: dict[str, str] = {}
        self._upstream_by_session: dict[str, str] = {}
        setattr(self.core, "_pipeline_event_control_enabled", True)

    @property
    def asr_pipeline(self):
        """返回 VL loop 按 turn 开闭使用的 ASR 转写器。

        主要功能：保留旧测试和外部调试代码通过 `app.agent_core.asr_pipeline`
        注入 ASR provider 的能力。turn 边界已交给 Silero，ASR 转写器现由 `VlAgentLoop`
        持有，因此注入目标是 loop 的 ASR 会话池。
        """

        return self.loop.asr_sessions

    @asr_pipeline.setter
    def asr_pipeline(self, value) -> None:
        """替换 VL loop 使用的 ASR 转写器。"""

        self.loop.asr_sessions = value

    def __getattr__(self, name: str):
        """兼容旧调用点读取内部 Vision core 属性。

        参数：`name` 为调用方读取的属性名。
        返回值：内部 core 上的同名属性。
        异常情况：内部 core 也不存在该属性时抛出 AttributeError。
        """

        return getattr(self.core, name)

    def __setattr__(self, name: str, value) -> None:
        """兼容旧调用点写入内部 Vision core 属性。

        主要逻辑：runtime 自身字段仍写在 wrapper 上；当内部 core 已存在同名字段时，
        写入 core，避免测试或应用注入 provider 时只改到 wrapper。
        """

        runtime_fields = {
            "core",
            "loop",
            "speech_boundary",
            "emitter",
            "output_delta_bridge",
            "turn_controller",
            "_session_by_user",
            "_upstream_by_session",
        }
        if name in runtime_fields or "core" not in self.__dict__:
            object.__setattr__(self, name, value)
            return
        if name == "asr_pipeline":
            self.loop.asr_sessions = value
            return
        if hasattr(self.core, name):
            setattr(self.core, name, value)
            return
        object.__setattr__(self, name, value)

    def bind_pipeline_event_handler(self, handler) -> None:
        """绑定 app 层 pipeline event 处理器。"""

        self.emitter.add_listener(handler)

    def bind_tool_gateway(self, tool_gateway: ToolGateway) -> None:
        """绑定工具网关。"""

        self.core.bind_tool_gateway(tool_gateway)

    def bind_user_activity_callback(self, callback: Callable[[str, str], None]) -> None:
        """绑定有效用户语音活动回调。"""

        self.core.bind_user_activity_callback(callback)

    def open(self, user_id: str | ConversationContext, session_id: str | None = None) -> None:
        """打开 VL conversation 会话。

        主要逻辑：兼容旧 `open(user_id, session_id)` 调用，同时支持设计文档中的
        `open(context)` AgentCore 入口。
        """

        if isinstance(user_id, ConversationContext):
            context = user_id
            user_id = context.user_id
            session_id = context.session_id
        if session_id is None:
            raise ValueError("session_id is required when opening VL conversation without context")

        self._session_by_user[user_id] = session_id
        self.core.open(user_id, session_id)
        self.emitter.emit("session_ready", user_id=user_id, session_id=session_id)

    def open_context(self, context: ConversationContext) -> None:
        """按 AgentCoreABC 上下文打开 VL conversation 会话。"""

        self.open(context.user_id, context.session_id)

    def open_agent(self, context: ConversationContext) -> None:
        """按设计文档中的 AgentContext 打开 VL conversation 会话。"""

        self.open_context(context)

    def append_audio_event(self, chunk: StreamChunk) -> None:
        """消费归一化后的麦克风音频。"""

        for delta in self.speech_boundary.append_audio(chunk):
            self._consume_speech_delta(delta)

    def consume_input(self, delta: SpeechInputDelta) -> None:
        """消费标准语音输入增量。

        主要逻辑：作为 `AgentCoreABC.consume_input` 的 conversation 实现入口，
        允许后续 runtime 绕过旧 `append_audio_event()` 直接送入标准输入事件。
        参数：`delta` 为语音输入边界输出的标准增量。
        返回值：无。
        异常情况：链路专属 core 异常会向上传播。
        """

        self._consume_speech_delta(delta)

    def consume_task_signal(self, signal: TaskSignal) -> None:
        """消费长生命周期 Task 回流信号。

        当前 VL conversation runtime 尚未把 TaskSignal 直接注入下一轮 VLM 上下文；
        先写入 conversation 事件，保证信号进入 runs 并可被后续 loop 策略接管。
        """

        self.emitter.emit(
            "task_signal_received",
            user_id=signal.user_id or "",
            session_id=signal.session_id,
            kind=signal.kind,
            payload=dict(signal.payload),
            metadata=dict(signal.metadata),
        )

    def commit_input(self, user_id: str, session_id: str, *, reason: str = "endpoint_commit") -> None:
        """兼容旧 AgentCore 输入提交入口。"""

        self.core.commit_input(user_id, session_id, reason=reason)

    def interrupt(self, user_id: str, *, reason: str) -> None:
        """取消当前 VL 回复和输出。"""

        self.core.interrupt(user_id, reason=reason)

    def close(self, user_id: str, *, reason: str) -> None:
        """关闭当前 VL conversation 会话。"""

        self.core.close(user_id, reason=reason)
        session_id = self._session_by_user.pop(user_id, "")
        if session_id:
            self.loop.close_session(session_id)

    def events(self) -> list[AgentCoreEvent]:
        """返回内部 Vision core 事件快照。"""

        return self.core.events()

    def snapshot(self) -> AgentSnapshot:
        """返回 VL conversation runtime 只读状态快照。"""

        user_id = next(iter(self._session_by_user.keys()), None)
        session_id = self._session_by_user.get(user_id or "") if user_id else None
        active_streams = {}
        if session_id and self._upstream_by_session.get(session_id):
            active_streams["mic"] = self._upstream_by_session[session_id]
        return AgentSnapshot(
            user_id=user_id,
            session_id=session_id,
            mode="vision",
            state=self._state(user_id or "", session_id or "") if user_id and session_id else None,
            active_streams=active_streams,
        )

    def on_audio_input_opened(self, *, user_id: str, session_id: str, stream_id: str) -> None:
        """记录上行麦克风 stream 并提前连接 ASR provider。"""

        self._upstream_by_session[session_id] = stream_id
        self.core._audio_stream_by_session[session_id] = stream_id
        self.core._closed_audio_streams_by_session.get(session_id, set()).discard(stream_id)
        self.speech_boundary.prepare_provider(stream_id=stream_id, session_id=session_id)
        self.emitter.emit("upstream_ready", user_id=user_id, session_id=session_id, stream_id=stream_id)

    def on_audio_input_closed(self, *, user_id: str, session_id: str, stream_id: str, reason: str) -> None:
        """记录上行麦克风 stream 关闭。"""

        self._upstream_by_session.pop(session_id, None)
        self.speech_boundary.close_provider(stream_id=stream_id)
        self.loop.asr_sessions.close_provider(stream_id=stream_id)
        self.core.on_audio_input_closed(user_id=user_id, session_id=session_id, stream_id=stream_id, reason=reason)
        self.emitter.emit("upstream_detached", user_id=user_id, session_id=session_id, stream_id=stream_id, reason=reason)

    def on_downstream_opened(
        self,
        *,
        user_id: str,
        session_id: str,
        stream_id: str,
        stream_type: str = "actuator.speaker",
    ) -> None:
        """绑定下行扬声器 stream。"""

        self.emitter.emit(
            "downstream_ready",
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            stream_type=stream_type,
        )

    def pause_downstream(self, user_id: str, session_id: str) -> None:
        """暂停当前会话下行 speaker 写出。"""

        self.core.output_service.pause_session(user_id=user_id, session_id=session_id)
        self.emitter.emit("downstream_paused", user_id=user_id, session_id=session_id)

    def resume_downstream(self, user_id: str, session_id: str) -> None:
        """恢复当前会话下行 speaker 写出。"""

        self.core.output_service.resume_session(user_id=user_id, session_id=session_id)
        self.emitter.emit("downstream_resumed", user_id=user_id, session_id=session_id)

    def on_speech_started(
        self,
        user_id: str,
        session_id: str,
        *,
        stream_id: str = "",
        reason: str = "manual_speech_started",
        diagnostics: dict | None = None,
    ) -> None:
        """兼容旧 speech started 入口并转入正式 turn controller。"""

        self._consume_speech_delta(
            SpeechInputDelta(
                kind="turn_started",
                session_id=session_id,
                user_id=user_id,
                stream_id=stream_id,
                metadata={"legacy_reason": reason, **dict(diagnostics or {})},
            )
        )

    def on_speech_stopped(
        self,
        user_id: str,
        session_id: str,
        *,
        stream_id: str = "",
        reason: str = "manual_speech_stopped",
        diagnostics: dict | None = None,
    ) -> None:
        """兼容旧 speech stopped 入口并转入正式 turn controller。"""

        self._consume_speech_delta(
            SpeechInputDelta(
                kind="turn_ended",
                session_id=session_id,
                user_id=user_id,
                stream_id=stream_id,
                metadata={"legacy_reason": reason, **dict(diagnostics or {})},
            )
        )

    def _consume_speech_delta(self, delta: SpeechInputDelta) -> None:
        if delta.kind == "audio_chunk" and delta.audio is not None:
            self.turn_controller.observe_active_output(user_id=delta.user_id or "", session_id=delta.session_id)
            if delta.user_id:
                self.core._set_turn_state(delta.user_id, delta.session_id, "transcribing", reason="audio_chunk")
            self.loop.consume_input(delta)
            return
        if delta.kind == "asr_text_delta":
            self.loop.consume_input(delta)
            return
        if delta.kind == "turn_started":
            self._handle_turn_started(delta)
            return
        if delta.kind == "turn_ended":
            self._handle_turn_ended(delta)

    def _handle_turn_started(self, delta: SpeechInputDelta) -> None:
        """处理 ASR 句子开始边界。"""

        self.turn_controller.handle_turn_started(
            delta,
            reason=_turn_started_reason(delta),
        )

    def _sync_conversation_speech_started(
        self,
        *,
        user_id: str,
        session_id: str,
        stream_id: str,
        reason: str,
        diagnostics: dict,
    ) -> None:
        """同步 VL 语音开始到旧 core。"""

        self.core.on_speech_started(
            user_id,
            session_id,
            stream_id=stream_id,
            reason=reason,
            diagnostics=dict(diagnostics),
        )

    def _handle_turn_ended(self, delta: SpeechInputDelta) -> None:
        """处理 ASR 句子结束边界。"""

        context = self.turn_controller.handle_turn_ended(
            delta,
            reason=_turn_ended_reason(delta),
        )
        if context.ignored:
            return
        self.loop.consume_input(
            SpeechInputDelta(
                kind="turn_ended",
                session_id=context.session_id,
                user_id=context.user_id,
                stream_id=context.stream_id,
                final_text=delta.final_text,
                metadata=dict(delta.metadata),
            )
        )

    def _sync_conversation_speech_stopped(
        self,
        *,
        user_id: str,
        session_id: str,
        stream_id: str,
        reason: str,
        diagnostics: dict,
    ) -> None:
        """同步 VL 语音结束到旧 core。"""

        self.core.on_conversation_speech_stopped(
            user_id,
            session_id,
            stream_id=stream_id,
            reason=reason,
            diagnostics=dict(diagnostics),
        )

    def _active_output_stream_id(self, user_id: str, session_id: str) -> str | None:
        """查询当前 VL 会话活跃 output stream。"""

        return self.core.output_service.active_output_stream_id(user_id, session_id)

    def _state(self, user_id: str, session_id: str) -> str:
        """查询当前 VL 用户生成状态。"""

        return str(getattr(self.core, "_state_by_user", {}).get(user_id, "") or "")


def _turn_started_reason(delta: SpeechInputDelta) -> str:
    """根据 ASR/VAD 元数据恢复 turn started 来源。"""

    legacy_reason = str(delta.metadata.get("legacy_reason") or "")
    if legacy_reason:
        return legacy_reason
    if delta.metadata.get("asr_boundary") == "sentence_begin":
        return "paraformer_sentence_begin"
    return "conversation_asr_speech_started"


def _turn_ended_reason(delta: SpeechInputDelta) -> str:
    """根据 ASR/VAD 元数据恢复 turn ended 来源。"""

    legacy_reason = str(delta.metadata.get("legacy_reason") or "")
    if legacy_reason:
        return legacy_reason
    if delta.metadata.get("asr_boundary") == "sentence_end":
        return "paraformer_sentence_end"
    return "conversation_asr_speech_stopped"
