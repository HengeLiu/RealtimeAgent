from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from realtime_agent.agent_core.providers import AsrProviderConfig
from realtime_agent.agent_core.vision import AsrPipeline
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

    def __init__(self, *, config: AsrProviderConfig | None = None, recorder: RunRecorder) -> None:
        self._pending_events_by_stream: dict[str, list[Any]] = {}
        self.asr_pipeline = AsrPipeline(
            config=config or AsrProviderConfig(),
            recorder=recorder,
            on_transcript_event=self._collect_asr_event,
        )
        self._started_sentence_keys: set[str] = set()
        self._stopped_sentence_keys: set[str] = set()

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
        self._started_sentence_keys.clear()
        self._stopped_sentence_keys.clear()

    def _event_to_deltas(self, chunk: StreamChunk, event: Any) -> Iterator[SpeechInputDelta]:
        """把单个 ASR 事件转换为 conversation 输入增量。"""

        metadata = self._asr_metadata(event)
        sentence_key = self._sentence_key(chunk=chunk, event=event)
        if bool(getattr(event, "sentence_begin", False)) and sentence_key not in self._started_sentence_keys:
            self._started_sentence_keys.add(sentence_key)
            yield SpeechInputDelta(
                kind="turn_started",
                session_id=chunk.session_id,
                user_id=chunk.user_id,
                stream_id=chunk.stream_id,
                metadata={"asr_boundary": "sentence_begin", **metadata},
            )
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
        if (sentence_end or final) and sentence_key not in self._stopped_sentence_keys:
            self._stopped_sentence_keys.add(sentence_key)
            yield SpeechInputDelta(
                kind="turn_ended",
                session_id=chunk.session_id,
                user_id=chunk.user_id,
                stream_id=chunk.stream_id,
                final_text=text,
                metadata={"asr_boundary": "sentence_end" if sentence_end else "final", **metadata},
            )

    def _sentence_key(self, *, chunk: StreamChunk, event: Any) -> str:
        """生成 ASR 句子边界去重 key。"""

        sentence_id = getattr(event, "sentence_id", None)
        if sentence_id is None:
            sentence_id = f"seq:{chunk.seq}"
        return f"{chunk.session_id}:{chunk.stream_id}:{sentence_id}"

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
