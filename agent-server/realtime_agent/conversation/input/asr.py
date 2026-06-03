from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from realtime_agent.agent_core.providers import AsrProviderConfig
from realtime_agent.agent_core.vision import AsrPipeline
from realtime_agent.conversation.input.vad import AsrVoiceActivityBoundary, SpeechBoundaryDelta
from realtime_agent.conversation.types import SpeechInputDelta
from realtime_agent.observability import RunRecorder
from realtime_agent.protocol import StreamChunk


class AsrSpeechInputBoundary:
    """基于 ASR 事件的 conversation 语音输入边界。

    主要功能：包装现有 ASR provider，把音频片、ASR 文本增量和句子边界统一转换成
    `SpeechInputDelta`。Paraformer 这类 ASR/VAD 合一模型的 `sentence_begin` 和
    `sentence_end` 会分别映射为 `turn_started` 和 `turn_ended(final_text)`。
    主要属性：`asr_pipeline` 复用旧 Vision 链路的 ASR provider 管理和事件落盘逻辑。
    """

    def __init__(
        self,
        *,
        config: AsrProviderConfig | None = None,
        recorder: RunRecorder,
        voice_boundary: AsrVoiceActivityBoundary | None = None,
    ) -> None:
        self._pending_events_by_stream: dict[str, list[Any]] = {}
        self.asr_pipeline = AsrPipeline(
            config=config or AsrProviderConfig(),
            recorder=recorder,
            on_transcript_event=self._collect_asr_event,
        )
        self.voice_boundary = voice_boundary or AsrVoiceActivityBoundary()

    def prepare_provider(self, *, stream_id: str, session_id: str | None = None) -> None:
        """提前建立当前麦克风 stream 的 ASR provider。"""

        self.asr_pipeline.prepare_provider(stream_id=stream_id, session_id=session_id)

    def close_provider(self, *, stream_id: str) -> None:
        """关闭当前麦克风 stream 的 ASR provider。"""

        self.asr_pipeline.close_provider(stream_id=stream_id)

    def append_audio(self, chunk: StreamChunk) -> Iterator[SpeechInputDelta]:
        """追加一片音频并输出 ASR 驱动的语音输入增量。

        主要逻辑：先输出原始 `audio_chunk`，随后把 ASR partial/final 和句边界适配成
        `asr_text_delta`、`turn_started`、`turn_ended`。对于没有结构化句边界的 provider，
        final 文本会兜底映射为 `turn_ended(final_text)`。
        参数：`chunk` 为规范化后的麦克风音频。
        返回值：语音输入增量迭代器。
        异常情况：ASR provider 异常沿用现有 `AsrPipeline` 降级或抛错策略。
        """

        yield SpeechInputDelta(
            kind="audio_chunk",
            session_id=chunk.session_id,
            user_id=chunk.user_id,
            stream_id=chunk.stream_id,
            audio=chunk,
        )
        provider_key = chunk.stream_id or chunk.session_id
        self._pending_events_by_stream[provider_key] = []
        self.asr_pipeline.append_audio(chunk)
        events = self._pending_events_by_stream.pop(provider_key, [])
        for event in events:
            yield from self._event_to_deltas(chunk, event)

    def _collect_asr_event(self, chunk: StreamChunk, event: Any) -> None:
        """收集当前 append_audio 产生的 ASR 事件。"""

        provider_key = chunk.stream_id or chunk.session_id
        self._pending_events_by_stream.setdefault(provider_key, []).append(event)

    def cancel(self) -> None:
        """取消当前 ASR 输入边界。"""

        self.asr_pipeline.cancel()
        self.voice_boundary.reset()

    def _event_to_deltas(self, chunk: StreamChunk, event: Any) -> Iterator[SpeechInputDelta]:
        """把单个 ASR 事件转换为 conversation 输入增量。"""

        metadata = self._asr_metadata(event)
        boundaries = self.voice_boundary.append_asr_event(chunk=chunk, event=event)
        for boundary in boundaries:
            if boundary.kind == "speech_started":
                yield _boundary_to_speech_input_delta(boundary)
        text = str(getattr(event, "text", "") or "")
        final = bool(getattr(event, "final", False))
        sentence_end = bool(getattr(event, "sentence_end", False))
        if text and not final and not sentence_end:
            yield SpeechInputDelta(
                kind="asr_text_delta",
                session_id=chunk.session_id,
                user_id=chunk.user_id,
                stream_id=chunk.stream_id,
                text_delta=text,
                metadata=metadata,
            )
        for boundary in boundaries:
            if boundary.kind != "speech_stopped":
                continue
            delta = _boundary_to_speech_input_delta(boundary)
            yield SpeechInputDelta(
                kind=delta.kind,
                session_id=delta.session_id,
                user_id=delta.user_id,
                stream_id=delta.stream_id,
                final_text=text,
                turn_id=delta.turn_id,
                monotonic_ms=delta.monotonic_ms,
                metadata=delta.metadata,
            )

    @staticmethod
    def _asr_metadata(event: Any) -> dict[str, Any]:
        """提取可落盘的 ASR 诊断字段。"""

        metadata: dict[str, Any] = {}
        for key in (
            "sentence_id",
            "sentence_begin",
            "sentence_end",
            "begin_time_ms",
            "end_time_ms",
            "words",
            "final",
        ):
            value = getattr(event, key, None)
            if value not in (None, False, []):
                metadata[key] = value
        return metadata


def _boundary_to_speech_input_delta(boundary: SpeechBoundaryDelta) -> SpeechInputDelta:
    """把 ASR-backed speech 边界转换为 conversation 输入增量。

    主要逻辑：`speech_started` 转成 `turn_started`，`speech_stopped` 转成
    `turn_ended`，并保留 ASR 句边界诊断字段。
    参数：`boundary` 为 ASR 句边界适配后的语音边界。
    返回值：Agent Core 可消费的标准语音输入增量。
    异常情况：无。
    """

    kind = "turn_started" if boundary.kind == "speech_started" else "turn_ended"
    return SpeechInputDelta(
        kind=kind,
        session_id=boundary.session_id,
        user_id=boundary.user_id,
        stream_id=boundary.stream_id,
        metadata={"speech_boundary": boundary.kind, **dict(boundary.metadata)},
    )
