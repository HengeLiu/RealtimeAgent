from __future__ import annotations

from typing import Callable

from realtime_agent.conversation.core.omni_host import OmniRealtimeAgentCore
from realtime_agent.conversation.core.base import AgentCoreEvent, AgentSnapshot, ConversationContext
from realtime_agent.conversation.core.loop import OmniRealtimeLoop
from realtime_agent.conversation.input import AsrSpeechInputBoundary, CallbackVisualInputBoundary, SileroSpeechInputBoundary
from realtime_agent.conversation.events import ConversationRuntimeEventEmitter
from realtime_agent.conversation.output import ConversationOutputController
from realtime_agent.conversation.turn import OutputInterruptionController, RealtimeTurnController
from realtime_agent.conversation.types import SpeechInputDelta
from realtime_agent.observability import RunRecorder
from realtime_agent.output import OutputService
from realtime_agent.protocol import StreamChunk
from realtime_agent.tools import ToolGateway


class OmniManualConversationRuntime:
    """Omni Manual conversation runtime。

    主要功能：以旧 `AgentCore` 兼容接口接入 `RealtimeAgentApp`，内部使用
    `SpeechInputDelta` 驱动 Omni Manual 的输入提交和响应创建。
    主要属性：`core` 复用现有 `OmniRealtimeAgentCore`；`speech_boundary` 负责把
    Silero VAD 边界转换为 `audio_chunk/turn_started/turn_ended`。
    """

    def __init__(
        self,
        *,
        core: OmniRealtimeAgentCore,
        output_service: OutputService,
        recorder: RunRecorder,
        speech_boundary: AsrSpeechInputBoundary | SileroSpeechInputBoundary,
    ) -> None:
        self.core = core
        self.loop = OmniRealtimeLoop(core=core)
        setattr(self.core, "_conversation_provider_callbacks", self.loop.provider_callbacks)
        self.speech_boundary = speech_boundary
        self.output_controller = ConversationOutputController(output_service=output_service, recorder=recorder)
        self.emitter = ConversationRuntimeEventEmitter(recorder=recorder)
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

    @property
    def asr_pipeline(self):
        """返回正式语音输入边界使用的 ASR pipeline。

        Omni Silero boundary 不使用 ASR，该属性仅保留兼容旧调试入口。
        """

        return self.speech_boundary.asr_pipeline

    @asr_pipeline.setter
    def asr_pipeline(self, value) -> None:
        """替换正式语音输入边界使用的 ASR pipeline。"""

        self.speech_boundary.asr_pipeline = value

    def __getattr__(self, name: str):
        """兼容旧调用点读取内部 Omni core 属性。"""

        return getattr(self.core, name)

    def __setattr__(self, name: str, value) -> None:
        """兼容旧调用点写入内部 Omni core 属性。"""

        runtime_fields = {
            "core",
            "loop",
            "speech_boundary",
            "output_controller",
            "emitter",
            "turn_controller",
            "_session_by_user",
            "_upstream_by_session",
        }
        if name in runtime_fields or "core" not in self.__dict__:
            object.__setattr__(self, name, value)
            return
        if name == "asr_pipeline":
            self.speech_boundary.asr_pipeline = value
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
        """打开 Omni Manual 会话。

        主要逻辑：兼容旧 `open(user_id, session_id)` 调用，同时支持设计文档中的
        `open(context)` AgentCore 入口。
        """

        if isinstance(user_id, ConversationContext):
            context = user_id
            user_id = context.user_id
            session_id = context.session_id
        if session_id is None:
            raise ValueError("session_id is required when opening Omni Manual conversation without context")

        self._session_by_user[user_id] = session_id
        self.core.open(user_id, session_id)
        self.emitter.emit("session_ready", user_id=user_id, session_id=session_id)

    def open_context(self, context: ConversationContext) -> None:
        """按 AgentCoreABC 上下文打开 Omni Manual 会话。"""

        self.open(context.user_id, context.session_id)

    def open_agent(self, context: ConversationContext) -> None:
        """按设计文档中的 AgentContext 打开 Omni Manual 会话。"""

        self.open_context(context)

    def append_audio_event(self, chunk: StreamChunk) -> None:
        """消费归一化后的麦克风音频。

        主要逻辑：Silero boundary 在 `turn_started` 后才输出 `audio_chunk`，
        因此只有真实语音 turn 内的 pre-roll 和后续音频会 append 给 Omni provider；
        `turn_started` 负责语音开始通知、视觉采样和打断请求；`turn_ended`
        显式提交输入并创建响应。
        """

        for delta in self.speech_boundary.append_audio(chunk):
            self._consume_speech_delta(delta)

    def consume_input(self, delta: SpeechInputDelta) -> None:
        """消费标准语音输入增量。

        主要逻辑：作为 `AgentCoreABC.consume_input` 的 conversation 实现入口，
        保留旧 `append_audio_event()` 适配的同时让测试和后续 runtime 可以直接送入
        标准输入事件。
        参数：`delta` 为语音输入边界输出的标准增量。
        返回值：无。
        异常情况：链路专属 core 异常会向上传播。
        """

        self._consume_speech_delta(delta)

    def commit_input(self, user_id: str, session_id: str, *, reason: str = "endpoint_commit") -> None:
        """兼容旧 AgentCore 输入提交入口。"""

        self.core.commit_input(user_id, session_id, reason=reason)

    def create_response(
        self,
        user_id: str,
        session_id: str,
        *,
        reason: str = "manual_turn_ended",
        instructions: str | None = None,
    ) -> None:
        """显式创建 Omni provider 响应。"""

        self.core.create_response(user_id, session_id, reason=reason, instructions=instructions)

    def interrupt(self, user_id: str, *, reason: str) -> None:
        """取消当前响应和输出。"""

        existing_session = self._session_by_user.get(user_id)
        self.core.interrupt(user_id, reason=reason)
        if existing_session:
            self.output_controller.cancel_active_output(user_id=user_id, session_id=existing_session, reason=reason)

    def close(self, user_id: str, *, reason: str) -> None:
        """关闭当前 Omni Manual 会话。"""

        self.core.close(user_id, reason=reason)
        self._session_by_user.pop(user_id, None)

    def events(self) -> list[AgentCoreEvent]:
        """返回内部 Omni core 事件快照。"""

        return self.core.events()

    def snapshot(self) -> AgentSnapshot:
        """返回 Omni Manual runtime 只读状态快照。"""

        user_id = next(iter(self._session_by_user.keys()), None)
        session_id = self._session_by_user.get(user_id or "") if user_id else None
        active_streams = {}
        if session_id and self._upstream_by_session.get(session_id):
            active_streams["mic"] = self._upstream_by_session[session_id]
        return AgentSnapshot(
            user_id=user_id,
            session_id=session_id,
            mode="omni",
            state=self._state(user_id or "", session_id or "") if user_id and session_id else None,
            active_streams=active_streams,
        )

    def on_audio_input_opened(self, *, user_id: str, session_id: str, stream_id: str) -> None:
        """记录上行麦克风 stream。"""

        self._upstream_by_session[session_id] = stream_id
        self.speech_boundary.prepare_provider(stream_id=stream_id, session_id=session_id)
        self.core.on_audio_input_opened(user_id=user_id, session_id=session_id, stream_id=stream_id)
        self.emitter.emit("upstream_ready", user_id=user_id, session_id=session_id, stream_id=stream_id)

    def on_audio_input_closed(self, *, user_id: str, session_id: str, stream_id: str, reason: str) -> None:
        """记录上行麦克风 stream 关闭。"""

        self._upstream_by_session.pop(session_id, None)
        self.speech_boundary.close_provider(stream_id=stream_id)
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

        self.output_controller.bind_downstream(
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            reason="conversation_attach_downstream",
            prepare_text_output=False,
        )
        self.emitter.emit("downstream_ready", user_id=user_id, session_id=session_id, stream_id=stream_id)

    def prepare_close(self, user_id: str, session_id: str, *, reason: str) -> None:
        """准备关闭会话，停止接收新输出。"""

        self.output_controller.stop_accepting_new_output(session_id=session_id, reason=reason)
        self.emitter.emit("close_prepared", user_id=user_id, session_id=session_id, reason=reason)

    def _consume_speech_delta(self, delta: SpeechInputDelta) -> None:
        if delta.kind == "audio_chunk" and delta.audio is not None:
            self.turn_controller.observe_active_output(user_id=delta.user_id or "", session_id=delta.session_id)
            self.loop.consume_input(delta)
            return
        if delta.kind == "turn_started":
            self._handle_turn_started(delta)
            return
        if delta.kind == "turn_ended":
            self._handle_turn_ended(delta)

    def _handle_turn_started(self, delta: SpeechInputDelta) -> None:
        reason = str(delta.metadata.get("reason") or "conversation_vad_speech_started")
        self.turn_controller.handle_turn_started(
            delta,
            reason=reason,
        )

    def _handle_turn_ended(self, delta: SpeechInputDelta) -> None:
        reason = str(delta.metadata.get("reason") or "conversation_vad_speech_stopped")
        context = self.turn_controller.handle_turn_ended(
            delta,
            reason=reason,
        )
        if context.ignored:
            return
        self.loop.consume_input(
            SpeechInputDelta(
                kind="turn_ended",
                session_id=context.session_id,
                user_id=context.user_id,
                stream_id=context.stream_id,
                metadata=dict(delta.metadata),
            )
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
        """同步 Omni Manual 语音开始到旧 core。"""

        self.core.on_conversation_speech_started(
            user_id=user_id,
            session_id=session_id,
            reason=reason,
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
        """同步 Omni Manual 语音结束到旧 core。"""

        self.core.on_conversation_speech_stopped(
            user_id=user_id,
            session_id=session_id,
            reason=reason,
        )

    def _active_output_stream_id(self, user_id: str, session_id: str) -> str | None:
        """查询当前 Omni 会话活跃 output stream。"""

        return self.output_controller.active_output_stream_id(user_id=user_id, session_id=session_id)

    def _state(self, user_id: str, session_id: str) -> str:
        """查询当前 Omni 会话生成状态。"""

        return str(getattr(self.core, "_state_by_session", {}).get(session_id, "") or "")
