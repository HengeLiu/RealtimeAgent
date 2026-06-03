from __future__ import annotations

from typing import Any

from realtime_agent.conversation.recorder import output_delta_record
from realtime_agent.conversation.types import AgentOutputDelta
from realtime_agent.observability import RunRecorder
from realtime_agent.output import OutputService


class ConversationOutputDeltaBridge:
    """旧 OutputService 输出事件到 AgentOutputDelta 的观测桥。

    主要功能：在不重复播放、不改变旧 core 输出热路径的前提下，把真实下行音频
    分片和 output finish 事件记录为标准 `AgentOutputDelta`，便于 Omni/VL runs
    以同一输出契约复盘。
    主要属性：`output_service` 提供 output 事件监听；`recorder` 负责落盘。
    """

    def __init__(self, *, output_service: OutputService, recorder: RunRecorder) -> None:
        self.output_service = output_service
        self.recorder = recorder
        self._bound = False

    def bind(self) -> None:
        """注册 OutputService 监听器。

        主要逻辑：只绑定一次 audio delta 和 finish listener；后续旧 core 继续正常写
        OutputService，bridge 只额外记录标准输出 delta。
        返回值：无。
        异常情况：底层 listener 注册异常向上传播。
        """

        if self._bound:
            return
        self.output_service.add_output_audio_delta_listener(self._on_audio_delta)
        self.output_service.add_output_finished_listener(self._on_output_finished)
        self._bound = True

    def _on_audio_delta(self, record: dict[str, Any]) -> None:
        """把 OutputService audio delta 记录为 AgentOutputDelta。"""

        session_id = str(record.get("session_id") or "")
        if not session_id:
            return
        stream_format = record.get("stream_format") if isinstance(record.get("stream_format"), dict) else {}
        delta = AgentOutputDelta(
            kind="audio_chunk",
            session_id=session_id,
            output_id=str(record.get("stream_id") or "") or None,
            sample_rate_hz=int(stream_format.get("sample_rate") or 0) or None,
            metadata={
                "user_id": record.get("user_id"),
                "stream_id": record.get("stream_id"),
                "payload_size": record.get("payload_size"),
                "chunk_count": record.get("chunk_count"),
                "source_event": record.get("event"),
                "source": _output_source(record),
            },
        )
        self.recorder.record_conversation_event(
            session_id,
            {
                "event": "conversation.agent_output_delta",
                **output_delta_record(delta),
                "payload_size": record.get("payload_size"),
                "chunk_count": record.get("chunk_count"),
            },
        )

    def _on_output_finished(self, user_id: str, session_id: str, stream_id: str) -> None:
        """把 output finish 记录为 AgentOutputDelta。"""

        delta = AgentOutputDelta(
            kind="output_finished",
            session_id=session_id,
            output_id=stream_id,
            metadata={"user_id": user_id, "stream_id": stream_id},
        )
        self.recorder.record_conversation_event(
            session_id,
            {"event": "conversation.agent_output_delta", **output_delta_record(delta)},
        )


def _output_source(record: dict[str, Any]) -> str:
    """从 OutputService 事件中推断输出来源。"""

    if "native_audio" in record:
        return "native_audio"
    if "tts" in record:
        return "tts"
    return "unknown"
