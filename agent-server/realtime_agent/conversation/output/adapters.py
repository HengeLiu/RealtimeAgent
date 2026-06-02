from __future__ import annotations

from realtime_agent.observability import RunRecorder
from realtime_agent.output import OutputService


class ConversationOutputController:
    """conversation runtime 输出控制适配器。

    主要功能：把 conversation runtime 内部的下行绑定、暂停、恢复、取消和关闭语义
    转接到现有 `OutputService`，保证新旧链路继续共享同一套播放仲裁。
    主要属性：`output_service` 是实际输出服务，`recorder` 负责 runs 证据。
    """

    def __init__(self, *, output_service: OutputService, recorder: RunRecorder) -> None:
        self.output_service = output_service
        self.recorder = recorder
        self._paused_sessions: set[str] = set()
        self._closed_sessions: set[str] = set()
        self._downstream_by_session: dict[str, str] = {}

    def bind_downstream(
        self,
        *,
        user_id: str,
        session_id: str,
        stream_id: str,
        reason: str = "downstream_attached",
        prepare_text_output: bool = True,
    ) -> None:
        """绑定下行扬声器 stream。

        参数：`prepare_text_output` 为 True 时预热文本/TTS 输出会话；Omni 原生音频
        链路可关闭该选项。
        返回值：无。
        异常情况：下游 `OutputService` 异常会向调用方传播。
        """

        self._downstream_by_session[session_id] = stream_id
        self._closed_sessions.discard(session_id)
        if prepare_text_output:
            self.output_service.prepare_text_session(session_id, reason=reason)
        self.recorder.record_agent_event(
            session_id,
            {"event": "conversation_output.downstream_bound", "user_id": user_id, "stream_id": stream_id, "reason": reason},
        )

    def pause(self, *, user_id: str, session_id: str) -> None:
        """记录端侧下行高水位暂停请求。"""

        self._paused_sessions.add(session_id)
        self.output_service.pause_session(user_id=user_id, session_id=session_id)
        self.recorder.record_agent_event(session_id, {"event": "conversation_output.paused", "user_id": user_id})

    def resume(self, *, user_id: str, session_id: str) -> None:
        """记录端侧下行低水位恢复请求。"""

        self._paused_sessions.discard(session_id)
        self.output_service.resume_session(user_id=user_id, session_id=session_id)
        self.recorder.record_agent_event(session_id, {"event": "conversation_output.resumed", "user_id": user_id})

    def cancel_active_output(self, *, user_id: str, session_id: str, reason: str) -> None:
        """取消当前活跃 output stream。"""

        self.output_service.interrupt_user(user_id, session_id=session_id, reason=reason)

    def stop_accepting_new_output(self, *, session_id: str, reason: str) -> None:
        """标记 session 进入关闭阶段，不再接受新的输出。

        当前实现只记录状态和运行产物；更严格的写入门控仍由底层 `OutputService`
        和具体 Agent Core 控制。
        """

        self._closed_sessions.add(session_id)
        self.recorder.record_agent_event(session_id, {"event": "conversation_output.stop_accepting_new_output", "reason": reason})

    def close_text_session(self, *, session_id: str, reason: str) -> None:
        """关闭连续对话级文本/TTS provider。"""

        self.output_service.close_text_session(session_id, reason=reason)

    def active_output_stream_id(self, *, user_id: str, session_id: str) -> str | None:
        """返回当前活跃输出 stream id。"""

        return self.output_service.active_output_stream_id(user_id, session_id)
