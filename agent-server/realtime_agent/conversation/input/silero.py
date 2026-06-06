from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from realtime_agent.conversation.input.vad import SileroVoiceActivityBoundary, SpeechBoundaryDelta
from realtime_agent.conversation.types import SpeechInputDelta, SpeechInputDeltaKind
from realtime_agent.protocol import StreamChunk


@dataclass(slots=True)
class _PreRollState:
    """单个 stream 的 pre-roll 和活跃语音状态。

    主要功能：在 speech_started 前保留短窗口音频，start 后一次性 flush 给 Omni。
    主要属性：`chunks` 是待 flush 的音频片；`duration_ms` 是当前缓存总时长；
    `active` 表示该 stream 是否已经处于用户语音 turn 内。
    """

    chunks: list[StreamChunk] = field(default_factory=list)
    duration_ms: int = 0
    active: bool = False


class SileroSpeechInputBoundary:
    """基于 Silero ONNX VAD 的 Omni manual 语音输入边界。

    主要功能：用 Silero VAD 产生 `turn_started/turn_ended`，并在 start 前只缓存
    短 pre-roll；start 后才向下游输出 `audio_chunk`，stop 后立即停止输出音频并
    触发 commit/create 所需的 `turn_ended`。
    主要属性：`voice_boundary` 负责 VAD；`pre_roll_ms` 控制 start 后补发的音频窗口。
    """

    def __init__(
        self,
        *,
        pre_roll_ms: int = 1200,
        stop_wait_ms: int = 200,
        threshold: float = 0.5,
        voice_boundary: SileroVoiceActivityBoundary | None = None,
    ) -> None:
        self.pre_roll_ms = pre_roll_ms
        self.stop_wait_ms = stop_wait_ms
        self.voice_boundary = voice_boundary or SileroVoiceActivityBoundary(
            threshold=threshold,
            min_silence_duration_ms=stop_wait_ms,
        )
        self._state_by_stream: dict[str, _PreRollState] = {}
        self._asr_pipeline: Any = None

    @property
    def asr_pipeline(self) -> Any:
        """兼容旧调试入口。

        Silero boundary 不使用 ASR provider；该属性只用于避免旧测试或调试代码读取
        `runtime.asr_pipeline` 时失败。
        """

        return self._asr_pipeline

    @asr_pipeline.setter
    def asr_pipeline(self, value: Any) -> None:
        """兼容旧调试入口设置。"""

        self._asr_pipeline = value

    def prepare_provider(self, *, stream_id: str, session_id: str | None = None) -> None:
        """准备当前麦克风 stream。

        参数：`stream_id` 为上行音频流；`session_id` 仅用于接口兼容。
        返回值：无。
        异常情况：无。
        """

        key = stream_id or session_id or ""
        self._state_by_stream.setdefault(key, _PreRollState())
        prepare_stream = getattr(self.voice_boundary, "prepare_stream", None)
        if key and callable(prepare_stream):
            prepare_stream(stream_id=key)

    def close_provider(self, *, stream_id: str) -> None:
        """关闭当前麦克风 stream 并清理 VAD/pre-roll 状态。"""

        self._state_by_stream.pop(stream_id, None)
        self.voice_boundary.reset(stream_id=stream_id)

    def append_audio(self, chunk: StreamChunk) -> Iterator[SpeechInputDelta]:
        """追加一片音频并输出 Omni manual 所需的输入增量。

        主要逻辑：
        1. 非活跃状态只缓存 pre-roll，不向 Omni append。
        2. Silero 输出 speech_started 后，先输出 `turn_started`，再 flush pre-roll
           为 `audio_chunk`。
        3. 活跃状态持续输出 `audio_chunk`。
        4. Silero 输出 speech_stopped 后，停止输出当前静音 chunk，并输出
           `turn_ended` 让 loop 立即 commit/create。
        参数：`chunk` 为规范化后的麦克风音频。
        返回值：`SpeechInputDelta` 迭代器。
        异常情况：VAD 模型异常会向上抛出。
        """

        key = self._stream_key(chunk)
        state = self._state_by_stream.setdefault(key, _PreRollState())
        was_active = state.active
        if not was_active:
            self._append_pre_roll(state, chunk)
        boundaries = self.voice_boundary.append_audio(chunk)
        started = [boundary for boundary in boundaries if boundary.kind == "speech_started"]
        stopped = [boundary for boundary in boundaries if boundary.kind == "speech_stopped"]

        if started and not was_active:
            state.active = True
            yield self._boundary_to_delta(started[0], kind="turn_started", extra={"reason": "conversation_vad_speech_started"})
            for buffered in state.chunks:
                yield self._audio_delta(buffered)
            state.chunks.clear()
            state.duration_ms = 0
        elif was_active and not stopped:
            yield self._audio_delta(chunk)

        if stopped and state.active:
            state.active = False
            state.chunks.clear()
            state.duration_ms = 0
            yield self._boundary_to_delta(stopped[-1], kind="turn_ended", extra={"reason": "conversation_vad_speech_stopped"})

    def cancel(self) -> None:
        """取消当前输入边界并清理缓存。"""

        self._state_by_stream.clear()
        self.voice_boundary.reset()

    @staticmethod
    def _stream_key(chunk: StreamChunk) -> str:
        return chunk.stream_id or chunk.session_id

    def _append_pre_roll(self, state: _PreRollState, chunk: StreamChunk) -> None:
        state.chunks.append(chunk)
        state.duration_ms += _chunk_duration_ms(chunk)
        while state.chunks and state.duration_ms > self.pre_roll_ms:
            removed = state.chunks.pop(0)
            state.duration_ms -= _chunk_duration_ms(removed)

    @staticmethod
    def _audio_delta(chunk: StreamChunk) -> SpeechInputDelta:
        return SpeechInputDelta(
            kind="audio_chunk",
            session_id=chunk.session_id,
            user_id=chunk.user_id,
            stream_id=chunk.stream_id,
            audio=chunk,
            metadata={"speech_boundary": "silero_active_audio"},
        )

    @staticmethod
    def _boundary_to_delta(
        boundary: SpeechBoundaryDelta,
        *,
        kind: SpeechInputDeltaKind,
        extra: dict[str, Any],
    ) -> SpeechInputDelta:
        return SpeechInputDelta(
            kind=kind,
            session_id=boundary.session_id,
            user_id=boundary.user_id,
            stream_id=boundary.stream_id,
            metadata={**dict(boundary.metadata), **extra},
        )


def _chunk_duration_ms(chunk: StreamChunk) -> int:
    """估算 chunk 时长。

    参数：`chunk` 为 PCM16 mono 16k 音频。
    返回值：优先使用 chunk.duration_ms；缺失时按 payload 长度估算。
    异常情况：无。
    """

    if chunk.duration_ms:
        return int(chunk.duration_ms)
    if not chunk.payload:
        return 0
    return int(round((len(chunk.payload) / 2) * 1000 / 16_000))
