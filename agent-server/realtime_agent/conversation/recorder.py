from __future__ import annotations

from realtime_agent.conversation.types import AgentOutputDelta, SpeechInputDelta


def speech_delta_record(delta: SpeechInputDelta) -> dict:
    """把语音输入增量转换为 runs 可记录结构。

    主要逻辑：只记录可审计的轻量字段，不把完整音频 bytes 写入控制类事件记录。
    参数：`delta` 为语音输入增量。
    返回值：可 JSON 序列化的字典。
    异常情况：无。
    """

    return {
        "kind": delta.kind,
        "session_id": delta.session_id,
        "user_id": delta.user_id,
        "stream_id": delta.stream_id,
        "turn_id": delta.turn_id,
        "text_delta": delta.text_delta,
        "final_text": delta.final_text,
        "monotonic_ms": delta.monotonic_ms,
        "audio_seq": delta.audio.seq if delta.audio is not None else None,
        "metadata": dict(delta.metadata),
    }


def output_delta_record(delta: AgentOutputDelta) -> dict:
    """把 Agent 输出增量转换为 runs 可记录结构。

    主要逻辑：原生音频只记录字节长度和采样率，不把音频 bytes 写入事件 JSON。
    参数：`delta` 为 Agent 输出增量。
    返回值：可 JSON 序列化的字典。
    异常情况：无。
    """

    return {
        "kind": delta.kind,
        "session_id": delta.session_id,
        "priority": delta.priority,
        "output_id": delta.output_id,
        "text_delta": delta.text_delta,
        "payload": None if isinstance(delta.payload, bytes) else delta.payload,
        "payload_bytes": len(delta.payload) if isinstance(delta.payload, bytes) else 0,
        "audio_bytes": len(delta.audio) if delta.audio is not None else 0,
        "sample_rate_hz": delta.sample_rate_hz,
        "metadata": dict(delta.metadata),
    }
