from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from realtime_agent.protocol import StreamChunk


SpeechBoundaryKind = Literal["speech_started", "speech_stopped"]


@dataclass(frozen=True, slots=True)
class SpeechBoundaryDelta:
    """语音活动边界增量。

    主要功能：表达 ASR/VAD 合一 provider 句边界转换后的两类语音边界事件。
    主要属性：`kind` 是边界类型；`metadata` 保存 ASR 句子编号、起止时间等诊断信息。
    """

    kind: SpeechBoundaryKind
    session_id: str
    user_id: str | None = None
    stream_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class AsrVoiceActivityBoundary:
    """ASR-backed 语音活动边界。

    主要功能：把 ASR/VAD 合一 provider 的 `sentence_begin` 和
    `sentence_end/final` 事件转换成统一 `SpeechBoundaryDelta`。
    ASR 文本本身不从该组件输出。
    """

    def __init__(self) -> None:
        self._started_sentence_keys: set[str] = set()
        self._stopped_sentence_keys: set[str] = set()

    def append_audio(self, chunk: StreamChunk) -> list[SpeechBoundaryDelta]:
        """追加音频。

        主要逻辑：ASR-backed 边界依赖 provider 结构化事件，因此直接追加音频不会
        产生边界。
        参数：`chunk` 为当前音频片。
        返回值：空列表。
        异常情况：无。
        """

        return []

    def append_asr_event(self, *, chunk: StreamChunk, event: Any) -> list[SpeechBoundaryDelta]:
        """追加一个 ASR provider 事件并返回 speech 边界。

        主要逻辑：`sentence_begin` 映射成 `speech_started`；
        `sentence_end/final` 映射成 `speech_stopped`，并按 sentence key 去重。
        参数：`chunk` 是触发该事件的音频片；`event` 是 ASR provider 原始事件。
        返回值：本事件触发的 speech 边界列表。
        异常情况：无。
        """

        metadata = self._asr_metadata(event)
        sentence_key = self._sentence_key(chunk=chunk, event=event)
        deltas: list[SpeechBoundaryDelta] = []
        if bool(getattr(event, "sentence_begin", False)) and sentence_key not in self._started_sentence_keys:
            self._started_sentence_keys.add(sentence_key)
            deltas.append(
                SpeechBoundaryDelta(
                    kind="speech_started",
                    session_id=chunk.session_id,
                    user_id=chunk.user_id,
                    stream_id=chunk.stream_id,
                    metadata={"asr_boundary": "sentence_begin", **metadata},
                )
            )
        sentence_end = bool(getattr(event, "sentence_end", False))
        final = bool(getattr(event, "final", False))
        if (sentence_end or final) and sentence_key not in self._stopped_sentence_keys:
            self._stopped_sentence_keys.add(sentence_key)
            deltas.append(
                SpeechBoundaryDelta(
                    kind="speech_stopped",
                    session_id=chunk.session_id,
                    user_id=chunk.user_id,
                    stream_id=chunk.stream_id,
                    metadata={"asr_boundary": "sentence_end" if sentence_end else "final", **metadata},
                )
            )
        return deltas

    def reset(self) -> None:
        """清空 ASR 句边界去重状态。"""

        self._started_sentence_keys.clear()
        self._stopped_sentence_keys.clear()

    def flush(self) -> list[SpeechBoundaryDelta]:
        """刷新边界来源。

        当前 ASR-backed 边界没有额外缓存，返回空列表。
        """

        return []

    @staticmethod
    def _sentence_key(*, chunk: StreamChunk, event: Any) -> str:
        sentence_id = getattr(event, "sentence_id", None)
        if sentence_id is None:
            sentence_id = f"seq:{chunk.seq}"
        return f"{chunk.session_id}:{chunk.stream_id}:{sentence_id}"

    @staticmethod
    def _asr_metadata(event: Any) -> dict[str, Any]:
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
