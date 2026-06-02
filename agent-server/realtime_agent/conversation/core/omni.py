from __future__ import annotations

from typing import Callable

from realtime_agent.agent_core.base import AgentCoreEvent
from realtime_agent.agent_core.omni import OmniRealtimeAgentCore
from realtime_agent.conversation.input import ServerVadSpeechInputBoundary
from realtime_agent.conversation.events import ConversationRuntimeEventEmitter
from realtime_agent.conversation.output import ConversationOutputController
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
    连续音频转换为 `audio_chunk/turn_started/turn_ended`。
    """

    def __init__(
        self,
        *,
        core: OmniRealtimeAgentCore,
        output_service: OutputService,
        recorder: RunRecorder,
        speech_boundary: ServerVadSpeechInputBoundary | None = None,
    ) -> None:
        self.core = core
        self.speech_boundary = speech_boundary or ServerVadSpeechInputBoundary()
        self.output_controller = ConversationOutputController(output_service=output_service, recorder=recorder)
        self.emitter = ConversationRuntimeEventEmitter(recorder=recorder)
        self._session_by_user: dict[str, str] = {}
        self._upstream_by_session: dict[str, str] = {}

    def bind_pipeline_event_handler(self, handler) -> None:
        """绑定 app 层 pipeline event 处理器。"""

        self.emitter.add_listener(handler)

    def bind_tool_gateway(self, tool_gateway: ToolGateway) -> None:
        """绑定工具网关。"""

        self.core.bind_tool_gateway(tool_gateway)

    def bind_user_activity_callback(self, callback: Callable[[str, str], None]) -> None:
        """绑定有效用户语音活动回调。"""

        self.core.bind_user_activity_callback(callback)

    def open(self, user_id: str, session_id: str) -> None:
        """打开 Omni Manual 会话。"""

        self._session_by_user[user_id] = session_id
        self.core.open(user_id, session_id)
        self.emitter.emit("session_ready", user_id=user_id, session_id=session_id)

    def append_audio_event(self, chunk: StreamChunk) -> None:
        """消费归一化后的麦克风音频。

        主要逻辑：`audio_chunk` 持续 append 给 Omni provider；`turn_started` 只负责
        语音开始通知和打断请求；`turn_ended` 显式提交输入并创建响应。
        """

        for delta in self.speech_boundary.append_audio(chunk):
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

    def on_audio_input_opened(self, *, user_id: str, session_id: str, stream_id: str) -> None:
        """记录上行麦克风 stream。"""

        self._upstream_by_session[session_id] = stream_id
        self.core.on_audio_input_opened(user_id=user_id, session_id=session_id, stream_id=stream_id)
        self.emitter.emit("upstream_ready", user_id=user_id, session_id=session_id, stream_id=stream_id)

    def on_audio_input_closed(self, *, user_id: str, session_id: str, stream_id: str, reason: str) -> None:
        """记录上行麦克风 stream 关闭。"""

        self._upstream_by_session.pop(session_id, None)
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
            self.core.append_audio_event(delta.audio)
            return
        if delta.kind == "turn_started":
            self._handle_turn_started(delta)
            return
        if delta.kind == "turn_ended":
            self._handle_turn_ended(delta)

    def _handle_turn_started(self, delta: SpeechInputDelta) -> None:
        session_id = delta.session_id
        user_id = delta.user_id or ""
        stream_id = delta.stream_id or self._upstream_by_session.get(session_id, "")
        self.emitter.emit(
            "speech_started",
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            reason="conversation_vad_speech_started",
            diagnostics=dict(delta.metadata),
        )
        self.core.on_conversation_speech_started(
            user_id=user_id,
            session_id=session_id,
            reason="conversation_vad_speech_started",
        )
        active_output_stream_id = self.output_controller.active_output_stream_id(user_id=user_id, session_id=session_id)
        state = getattr(self.core, "_state_by_session", {}).get(session_id, "")
        if active_output_stream_id is not None or state in {"thinking", "speaking", "tool_running"}:
            self.emitter.emit(
                "output_cancel_requested",
                user_id=user_id,
                session_id=session_id,
                stream_id=active_output_stream_id or "",
                reason="conversation_vad_speech_started",
                output_stream_id=active_output_stream_id,
                state=state,
            )

    def _handle_turn_ended(self, delta: SpeechInputDelta) -> None:
        session_id = delta.session_id
        user_id = delta.user_id or ""
        stream_id = delta.stream_id or self._upstream_by_session.get(session_id, "")
        self.emitter.emit(
            "speech_stopped",
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            reason="conversation_vad_speech_stopped",
            diagnostics=dict(delta.metadata),
        )
        reason = "conversation_vad_speech_stopped"
        self.core.on_conversation_speech_stopped(user_id=user_id, session_id=session_id, reason=reason)
        self.core.commit_input(user_id, session_id, reason=reason)
        self.core.on_conversation_input_committed(session_id=session_id, reason=reason)
        self.core.create_response(user_id, session_id, reason=reason)
