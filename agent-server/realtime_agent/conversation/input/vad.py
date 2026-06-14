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


@dataclass(slots=True)
class _SileroStreamState:
    """Silero VAD 单个音频流状态。

    主要功能：保存单个 stream 的 Silero iterator 和不足一帧的 PCM 缓存。
    主要属性：`iterator` 持有 Silero streaming 状态；`pending_pcm` 保存未满
    512 samples 的尾部音频；噪声底跟踪用于能量门控。
    """

    iterator: Any
    pending_pcm: bytearray = field(default_factory=bytearray)
    noise_floor_peak: float = 0.0
    noise_floor_rms: float = 0.0
    frame_count: int = 0
    is_speech: bool = False
    silence_frames: int = 0  # consecutive frames below noise floor


class SileroVoiceActivityBoundary:
    """Silero ONNX 语音活动边界。

    主要功能：把连续 PCM16 mono 16k 麦克风音频转换为 `speech_started` /
    `speech_stopped` 两类边界事件。该组件只负责 VAD，不负责打断、commit、
    response.create 或视觉采样策略。
    主要属性：`threshold` 是 Silero 语音概率阈值；`min_silence_duration_ms`
    是 stop wait；`speech_pad_ms` 用于 Silero 返回边界时的轻量 padding。
    """

    frame_samples = 512
    sample_rate = 16_000

    def __init__(
        self,
        *,
        threshold: float = 0.5,
        min_silence_duration_ms: int = 200,
        speech_pad_ms: int = 30,
        model_factory: Any | None = None,
        iterator_factory: Any | None = None,
    ) -> None:
        self.threshold = threshold
        self.min_silence_duration_ms = min_silence_duration_ms
        self.speech_pad_ms = speech_pad_ms
        self._model_factory = model_factory
        self._iterator_factory = iterator_factory
        self._state_by_stream: dict[str, _SileroStreamState] = {}

    def append_audio(self, chunk: StreamChunk) -> list[SpeechBoundaryDelta]:
        """追加音频并返回 Silero VAD 边界。

        主要逻辑：把 PCM16LE payload 缓存到 512 samples 一帧后送入 Silero ONNX
        iterator；iterator 返回 start/end 时转换为统一 `SpeechBoundaryDelta`。
        参数：`chunk` 为 AudioPipeline 归一化后的 sensor.mic PCM16 mono 16k 音频。
        返回值：本次音频触发的 speech 边界列表。
        异常情况：Silero 依赖缺失或模型调用失败时向上抛出，便于启动期暴露问题。
        """

        if not chunk.payload:
            return []
        state = self._state_for_stream(chunk.stream_id or chunk.session_id)
        state.pending_pcm.extend(chunk.payload)
        frame_bytes = self.frame_samples * 2
        deltas: list[SpeechBoundaryDelta] = []
        while len(state.pending_pcm) >= frame_bytes:
            frame = bytes(state.pending_pcm[:frame_bytes])
            del state.pending_pcm[:frame_bytes]
            float_frame = self._pcm16_frame_to_float(frame)
            # Energy-gated VAD for low-volume microphones (ESP32 PDM mic).
            # Problem: ESP32 PDM mic has very narrow dynamic range (noise ~0.044,
            # speech ~0.079). Any gain normalization that makes speech detectable
            # also amplifies noise above Silero's threshold, preventing speech_stopped.
            #
            # Solution: track noise floor, only feed "speech-like" frames (energy
            # significantly above noise floor) to Silero. Force speech_stopped after
            # enough consecutive silence frames.
            import numpy as np
            peak = float(abs(float_frame.max()))
            rms = float(np.sqrt(np.mean(float_frame.astype("float64") ** 2)))
            state.frame_count += 1

            # Update noise floor with slow EMA.
            # During first 200 frames (~4s): use very slow alpha to establish baseline.
            # During speech: almost no update (alpha=0.002) to prevent speech contaminating noise floor.
            # During silence: moderate update (alpha=0.03) to track noise floor changes.
            if state.frame_count <= 200:
                alpha = 0.02  # Slow convergence during warmup
            elif state.is_speech:
                alpha = 0.002  # Almost no update during speech
            else:
                alpha = 0.03  # Moderate update during silence
            state.noise_floor_peak = (1 - alpha) * state.noise_floor_peak + alpha * peak
            state.noise_floor_rms = (1 - alpha) * state.noise_floor_rms + alpha * rms

            # Determine if this frame is "speech-like" based on SNR.
            # ESP32 PDM mic has very narrow dynamic range (noise ~0.044, speech ~0.079),
            # so SNR threshold must be low. 1.5x RMS = ~3.5 dB SNR.
            nf_rms = max(state.noise_floor_rms, 0.0001)
            snr = rms / nf_rms
            is_speech_like = snr > 1.5  # 1.5x noise floor RMS = ~3.5 dB SNR

            if state.frame_count <= 10 or state.frame_count % 500 == 0:
                import logging as _logmod2
                _logmod2.getLogger("silero_vad").info(
                    "VAD frame #%d: peak=%.4f rms=%.4f nf_peak=%.4f nf_rms=%.4f snr=%.1f speech_like=%s is_speech=%s",
                    state.frame_count, peak, rms, state.noise_floor_peak,
                    state.noise_floor_rms, snr, is_speech_like, state.is_speech)

            if not is_speech_like:
                # Noise frame — don't feed to Silero, but count silence
                if state.is_speech:
                    state.silence_frames += 1
                    # Force speech_stopped after ~1s of silence (50 frames × 20ms)
                    if state.silence_frames >= 50:
                        state.is_speech = False
                        state.silence_frames = 0
                        import logging as _logmod3
                        _logmod3.getLogger("silero_vad").info(
                            "VAD FORCED speech_stopped: peak=%.4f rms=%.4f nf_rms=%.4f snr=%.1f silence_frames=%d",
                            peak, rms, nf_rms, snr, state.silence_frames)
                        deltas.append(
                            SpeechBoundaryDelta(
                                kind="speech_stopped",
                                session_id=chunk.session_id,
                                user_id=chunk.user_id,
                                stream_id=chunk.stream_id,
                                metadata={"vad_boundary": "speech_stopped", "reason": "energy_silence", "silence_frames": state.silence_frames},
                            )
                        )
                return deltas  # Skip Silero for noise frames

            # Speech-like frame — apply gain and feed to Silero
            state.silence_frames = 0
            gain = min(10.0, 0.6 / max(state.noise_floor_peak, 0.001))
            float_frame = float_frame * gain

            result = state.iterator(float_frame, return_seconds=False)
            if not result:
                continue
            import logging as _logmod
            _vadlog = _logmod.getLogger("silero_vad")
            _vadlog.info("VAD RESULT: peak=%.4f rms=%.4f nf_rms=%.4f snr=%.1f gain=%.1f result=%s",
                         peak, rms, nf_rms, snr,
                         min(10.0, 0.6 / max(state.noise_floor_peak, 0.001)), result)
            metadata = {
                "vad_provider": "silero_onnx",
                "threshold": self.threshold,
                "min_silence_duration_ms": self.min_silence_duration_ms,
                "speech_pad_ms": self.speech_pad_ms,
            }
            if "start" in result:
                state.is_speech = True
                state.silence_frames = 0
                deltas.append(
                    SpeechBoundaryDelta(
                        kind="speech_started",
                        session_id=chunk.session_id,
                        user_id=chunk.user_id,
                        stream_id=chunk.stream_id,
                        metadata={"vad_boundary": "speech_started", "start_sample": int(result["start"]), **metadata},
                    )
                )
            if "end" in result:
                state.is_speech = False
                state.silence_frames = 0
                deltas.append(
                    SpeechBoundaryDelta(
                        kind="speech_stopped",
                        session_id=chunk.session_id,
                        user_id=chunk.user_id,
                        stream_id=chunk.stream_id,
                        metadata={"vad_boundary": "speech_stopped", "end_sample": int(result["end"]), **metadata},
                    )
                )
        return deltas

    def prepare_stream(self, *, stream_id: str) -> None:
        """预热指定音频流的 Silero 状态。

        主要逻辑：在麦克风 stream 打开时提前创建 ONNX 模型和 streaming iterator，
        避免首次音频片到达时才加载模型。
        参数：`stream_id` 为上行音频流标识。
        返回值：无。
        异常情况：Silero 依赖缺失或模型加载失败时向上抛出。
        """

        self._state_for_stream(stream_id)

    def reset(self, *, stream_id: str | None = None) -> None:
        """清空 VAD 状态。

        参数：`stream_id` 为空时清空所有 stream；否则只清空指定 stream。
        返回值：无。
        异常情况：无。
        """

        if stream_id is None:
            self._state_by_stream.clear()
            return
        self._state_by_stream.pop(stream_id, None)

    def flush(self) -> list[SpeechBoundaryDelta]:
        """刷新边界来源。

        Silero streaming 边界不在 flush 时补发事件，返回空列表。
        """

        return []

    def _state_for_stream(self, stream_id: str) -> _SileroStreamState:
        state = self._state_by_stream.get(stream_id)
        if state is not None:
            return state
        model, iterator_cls = self._load_model_and_iterator()
        iterator = iterator_cls(
            model,
            threshold=self.threshold,
            sampling_rate=self.sample_rate,
            min_silence_duration_ms=self.min_silence_duration_ms,
            speech_pad_ms=self.speech_pad_ms,
        )
        state = _SileroStreamState(iterator=iterator)
        self._state_by_stream[stream_id] = state
        return state

    def _load_model_and_iterator(self) -> tuple[Any, Any]:
        if self._model_factory is not None and self._iterator_factory is not None:
            return self._model_factory(), self._iterator_factory
        from silero_vad import VADIterator, load_silero_vad

        return load_silero_vad(onnx=True), VADIterator

    @staticmethod
    def _pcm16_frame_to_float(frame: bytes) -> Any:
        import numpy as np

        return np.frombuffer(frame, dtype="<i2").astype("float32") / 32768.0
