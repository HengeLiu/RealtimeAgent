from __future__ import annotations

from typing import Callable

from realtime_agent.agent_core.base import AgentCoreEvent
from realtime_agent.agent_core.vision import VisionRealtimeAgentCore
from realtime_agent.conversation.events import ConversationRuntimeEventEmitter
from realtime_agent.conversation.input import AsrSpeechInputBoundary
from realtime_agent.conversation.types import SpeechInputDelta
from realtime_agent.observability import RunRecorder
from realtime_agent.protocol import StreamChunk
from realtime_agent.tools import ToolGateway


class VisionConversationRuntime:
    """VL conversation runtime。

    主要功能：以旧 `AgentCore` 兼容接口接入 `RealtimeAgentApp`，内部使用
    ASR-backed `SpeechInputDelta` 驱动 Vision/VL core。该 runtime 复用现有
    `VisionRealtimeAgentCore` 的上下文、工具、视觉资产、VLM 和 TTS 输出逻辑，只替换
    输入边界和 turn 控制。
    主要属性：`core` 是现有 Vision core；`speech_boundary` 负责把音频转换为
    `audio_chunk/asr_text_delta/turn_started/turn_ended`。
    """

    def __init__(
        self,
        *,
        core: VisionRealtimeAgentCore,
        recorder: RunRecorder,
        speech_boundary: AsrSpeechInputBoundary,
    ) -> None:
        self.core = core
        self.speech_boundary = speech_boundary
        self.emitter = ConversationRuntimeEventEmitter(recorder=recorder)
        self._session_by_user: dict[str, str] = {}
        self._upstream_by_session: dict[str, str] = {}
        self._latest_audio_by_session: dict[str, StreamChunk] = {}
        setattr(self.core, "_pipeline_event_control_enabled", True)

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
        """打开 VL conversation 会话。"""

        self._session_by_user[user_id] = session_id
        self.core.open(user_id, session_id)
        self.emitter.emit("session_ready", user_id=user_id, session_id=session_id)

    def append_audio_event(self, chunk: StreamChunk) -> None:
        """消费归一化后的麦克风音频。"""

        for delta in self.speech_boundary.append_audio(chunk):
            self._consume_speech_delta(delta)

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
            self._latest_audio_by_session.pop(session_id, None)

    def events(self) -> list[AgentCoreEvent]:
        """返回内部 Vision core 事件快照。"""

        return self.core.events()

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
        self.core.on_audio_input_closed(user_id=user_id, session_id=session_id, stream_id=stream_id, reason=reason)
        self.emitter.emit("upstream_detached", user_id=user_id, session_id=session_id, stream_id=stream_id, reason=reason)

    def _consume_speech_delta(self, delta: SpeechInputDelta) -> None:
        if delta.kind == "audio_chunk" and delta.audio is not None:
            self._latest_audio_by_session[delta.session_id] = delta.audio
            self.core._latest_audio_chunk_by_session[delta.session_id] = delta.audio
            return
        if delta.kind == "asr_text_delta":
            self._handle_asr_text_delta(delta)
            return
        if delta.kind == "turn_started":
            self._handle_turn_started(delta)
            return
        if delta.kind == "turn_ended":
            self._handle_turn_ended(delta)

    def _handle_asr_text_delta(self, delta: SpeechInputDelta) -> None:
        """处理 ASR 文本增量。"""

        self.core.on_conversation_asr_text_delta(
            delta.user_id or "",
            delta.session_id,
            stream_id=delta.stream_id or self._upstream_by_session.get(delta.session_id, ""),
            text=delta.text_delta or "",
            diagnostics=dict(delta.metadata),
        )

    def _handle_turn_started(self, delta: SpeechInputDelta) -> None:
        """处理 ASR 句子开始边界。"""

        session_id = delta.session_id
        user_id = delta.user_id or ""
        stream_id = delta.stream_id or self._upstream_by_session.get(session_id, "")
        self.emitter.emit(
            "speech_started",
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            reason="conversation_asr_speech_started",
            diagnostics=dict(delta.metadata),
        )
        self.core.on_speech_started(
            user_id,
            session_id,
            stream_id=stream_id,
            reason="conversation_asr_speech_started",
            diagnostics=dict(delta.metadata),
        )
        active_output_stream_id = self.core.output_service.active_output_stream_id(user_id, session_id)
        state = getattr(self.core, "_state_by_user", {}).get(user_id, "")
        if active_output_stream_id is not None or state in {"thinking", "speaking", "tool_running"}:
            self.emitter.emit(
                "output_cancel_requested",
                user_id=user_id,
                session_id=session_id,
                stream_id=active_output_stream_id or "",
                reason="conversation_asr_speech_started",
                output_stream_id=active_output_stream_id,
                state=state,
            )

    def _handle_turn_ended(self, delta: SpeechInputDelta) -> None:
        """处理 ASR 句子结束边界。"""

        session_id = delta.session_id
        user_id = delta.user_id or ""
        stream_id = delta.stream_id or self._upstream_by_session.get(session_id, "")
        self.emitter.emit(
            "speech_stopped",
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            reason="conversation_asr_speech_stopped",
            diagnostics=dict(delta.metadata),
        )
        self.core.on_conversation_speech_stopped(
            user_id,
            session_id,
            stream_id=stream_id,
            reason="conversation_asr_speech_stopped",
            diagnostics=dict(delta.metadata),
        )
        chunk = self._latest_audio_by_session.get(session_id)
        if chunk is None:
            return
        self.core.handle_conversation_final_text(
            chunk=chunk,
            final_text=delta.final_text or "",
            reason="conversation_asr_speech_stopped",
        )
