from __future__ import annotations

import audioop
import math
from dataclasses import dataclass
from typing import Protocol

from realtime_agent.protocol import StreamChunk


@dataclass(frozen=True)
class AudioPipelineConfig:
    expected_codec: str = "pcm16le"
    expected_sample_rate: int = 16000
    expected_channels: int = 1
    resample: str = "auto"
    volume_probe: bool = True
    vad: str = "diagnostic"
    vad_rms_threshold: int = 96
    vad_silence_timeout_ms: int = 600


@dataclass(frozen=True)
class AudioProcessorResult:
    """音频处理器结果。

    主要功能：携带处理后的 chunk 和诊断信息。
    主要属性：`chunk` 是下一个处理器要消费的音频片；`diagnostics` 是质量、降级或
    格式统计，只用于排障，不影响 Agent turn boundary。
    """

    chunk: StreamChunk
    diagnostics: dict


class AudioProcessor(Protocol):
    """Audio Pipeline 处理器接口。

    主要功能：约束所有音频处理器只接收并返回 `StreamChunk`，避免处理器直接访问设备
    连接或 Agent 运行时。
    主要方法：`process()` 返回处理结果和轻量诊断。
    """

    name: str

    def process(self, chunk: StreamChunk) -> AudioProcessorResult:
        ...


class FormatValidator:
    """sensor.mic 格式校验器。

    主要功能：确认输入是麦克风流，并校验 codec 与声道数满足服务端音频链路要求。
    主要方法：`process()` 在格式非法时抛出可读错误。
    主要属性：`config` 保存期望格式。
    """

    name = "format_validator"

    def __init__(self, config: AudioPipelineConfig | None = None) -> None:
        self.config = config or AudioPipelineConfig()

    def process(self, chunk: StreamChunk) -> AudioProcessorResult:
        """校验麦克风输入格式。

        主要逻辑：只接受 `sensor.mic` 和 `pcm16le`；采样率差异允许后续 resampler
        处理；声道数只允许 1 或 2，避免未知多声道布局被误处理。
        参数：`chunk` 为上行音频片。
        返回值：原始 `chunk` 与格式诊断。
        异常情况：stream 类型、codec 或声道不支持时抛出 `ValueError`。
        """

        if chunk.stream_type != "sensor.mic":
            raise ValueError("Audio Pipeline only accepts sensor.mic")
        if chunk.codec != self.config.expected_codec:
            raise ValueError("unsupported sensor.mic codec")
        if chunk.channels not in {1, 2}:
            raise ValueError("unsupported sensor.mic channels")
        if chunk.sample_rate <= 0:
            raise ValueError("unsupported sensor.mic sample_rate")
        return AudioProcessorResult(
            chunk=chunk,
            diagnostics={
                "codec": chunk.codec,
                "sample_rate": chunk.sample_rate,
                "channels": chunk.channels,
            },
        )


class FormatNormalizer:
    """历史测试名称的格式校验器。

    主要功能：提供 `FormatNormalizer` 适配类，同时内部复用 `FormatValidator`。
    主要方法：`process()` 返回校验后的 `StreamChunk`。
    主要属性：`validator` 是实际处理器。
    """

    def __init__(self, config: AudioPipelineConfig | None = None) -> None:
        self.validator = FormatValidator(config)

    def process(self, chunk: StreamChunk) -> StreamChunk:
        return self.validator.process(chunk).chunk


class Pcm16Resampler:
    """PCM16 轻量重采样器。

    主要功能：把端侧上行 PCM16 音频转成服务端期望采样率和声道数。
    主要方法：`process()` 使用 Python 标准库 `audioop` 做转换；依赖不可用或配置禁用
    时会明确写入降级诊断。
    主要属性：`config` 保存目标格式，`_state` 保存 `audioop.ratecv` 的连续状态。
    """

    name = "pcm16_resampler"

    def __init__(self, config: AudioPipelineConfig | None = None) -> None:
        self.config = config or AudioPipelineConfig()
        self._state = None

    def process(self, chunk: StreamChunk) -> AudioProcessorResult:
        """按需重采样 PCM16 音频。

        主要逻辑：`resample=disabled` 时只报告跳过；采样率或声道不同且可转换时生成新
        chunk；转换失败时抛出明确异常，避免配置声明启用但静默跳过。
        参数：`chunk` 为已通过格式校验的音频片。
        返回值：可能被转换后的 `StreamChunk` 与转换诊断。
        异常情况：配置要求转换但底层转换失败时抛出 `RuntimeError`。
        """

        if chunk.sample_rate == self.config.expected_sample_rate and chunk.channels == self.config.expected_channels:
            return AudioProcessorResult(chunk=chunk, diagnostics={"resampled": False, "reason": "already_target_format"})
        if self.config.resample in {"disabled", "off", "false"}:
            return AudioProcessorResult(
                chunk=chunk,
                diagnostics={
                    "resampled": False,
                    "degraded": True,
                    "reason": "resample_disabled",
                    "source_sample_rate": chunk.sample_rate,
                    "target_sample_rate": self.config.expected_sample_rate,
                },
            )

        payload = chunk.payload
        channels = chunk.channels
        try:
            if channels == 2 and self.config.expected_channels == 1:
                payload = audioop.tomono(payload, 2, 0.5, 0.5)
                channels = 1
            elif channels == 1 and self.config.expected_channels == 2:
                payload = audioop.tostereo(payload, 2, 1.0, 1.0)
                channels = 2
            elif channels != self.config.expected_channels:
                raise RuntimeError(f"unsupported channel conversion: {channels}->{self.config.expected_channels}")
            if chunk.sample_rate != self.config.expected_sample_rate:
                payload, self._state = audioop.ratecv(
                    payload,
                    2,
                    channels,
                    chunk.sample_rate,
                    self.config.expected_sample_rate,
                    self._state,
                )
        except Exception as exc:  # noqa: BLE001 - audioop may raise audioop.error
            raise RuntimeError(f"PCM16 resample failed: {exc}") from exc

        return AudioProcessorResult(
            chunk=StreamChunk(
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                stream_id=chunk.stream_id,
                stream_type=chunk.stream_type,
                seq=chunk.seq,
                payload=payload,
                codec=chunk.codec,
                sample_rate=self.config.expected_sample_rate,
                channels=self.config.expected_channels,
                duration_ms=chunk.duration_ms,
                timestamp_ms=chunk.timestamp_ms,
                version=chunk.version,
                final=chunk.final,
                metadata={**chunk.metadata, "audio_pipeline.resampled": True},
            ),
            diagnostics={
                "resampled": True,
                "source_sample_rate": chunk.sample_rate,
                "target_sample_rate": self.config.expected_sample_rate,
                "source_channels": chunk.channels,
                "target_channels": self.config.expected_channels,
                "payload_size": len(payload),
            },
        )


class VolumeProbe:
    """音量质量探针。

    主要功能：统计 PCM16 RMS、峰值和近似 dBFS，只写诊断信息，不改变音频。
    主要方法：`process()` 返回原 chunk。
    主要属性：无外部依赖。
    """

    name = "volume_probe"

    def process(self, chunk: StreamChunk) -> AudioProcessorResult:
        """统计音频音量。

        参数：`chunk` 为 PCM16 音频片。
        返回值：原 chunk 和音量统计。
        异常情况：空 payload 时返回静音统计，不抛异常。
        """

        if not chunk.payload:
            return AudioProcessorResult(chunk=chunk, diagnostics={"rms": 0, "peak": 0, "dbfs": None})
        rms = audioop.rms(chunk.payload, 2)
        peak = audioop.max(chunk.payload, 2)
        dbfs = None if rms <= 0 else round(20 * math.log10(rms / 32768), 2)
        return AudioProcessorResult(chunk=chunk, diagnostics={"rms": rms, "peak": peak, "dbfs": dbfs})


class QualityVadProbe:
    """音频质量 VAD 探针。

    主要功能：基于 RMS 判断当前片段是否近似静音，仅用于链路健康诊断。
    主要方法：`process()` 返回原 chunk。
    主要属性：`threshold` 是静音判断阈值。
    """

    name = "quality_vad_probe"

    def __init__(self, *, threshold: int = 96) -> None:
        self.threshold = threshold

    def process(self, chunk: StreamChunk) -> AudioProcessorResult:
        """生成轻量 VAD 诊断。

        主要逻辑：用 RMS 小于阈值判断 near_silence，只用于健康诊断，不参与 turn boundary。
        参数：`chunk` 为 PCM16 音频片。
        返回值：原 chunk 和 VAD 统计。
        异常情况：无。
        """

        rms = audioop.rms(chunk.payload, 2) if chunk.payload else 0
        return AudioProcessorResult(
            chunk=chunk,
            diagnostics={
                "near_silence": rms < self.threshold,
                "rms": rms,
                "threshold": self.threshold,
                "diagnostic_only": True,
            },
        )


class ServerVadProcessor:
    """服务端 VAD 处理器。

    主要功能：在 Vision realtime 链路中由服务器根据上行音频判断 speech_start 和
    speech_stop，避免浏览器端自行决定用户是否插话。
    主要方法：`process()` 基于 PCM16 RMS 阈值维护连续语音状态，并返回边界诊断。
    主要属性：`threshold` 是语音起点阈值，`silence_timeout_ms` 是结束语音所需的连续
    静音时长。
    """

    name = "server_vad"

    def __init__(self, *, threshold: int = 96, silence_timeout_ms: int = 600) -> None:
        self.threshold = max(1, int(threshold))
        self.silence_timeout_ms = max(20, int(silence_timeout_ms))
        self._in_speech = False
        self._silence_ms = 0

    def process(self, chunk: StreamChunk) -> AudioProcessorResult:
        """检测服务端语音边界。

        主要逻辑：PCM RMS 达到阈值时触发 speech_started；进入语音状态后，连续静音超过
        `silence_timeout_ms` 触发 speech_stopped。该处理器只负责生成边界事件，音频本身
        原样传给 Agent Core。
        参数：`chunk` 为经过格式校验和重采样后的麦克风音频。
        返回值：原 chunk 和 VAD 边界诊断。
        异常情况：空 payload 按静音处理。
        """

        rms = audioop.rms(chunk.payload, 2) if chunk.payload else 0
        chunk_ms = int(chunk.duration_ms or self._estimate_duration_ms(chunk))
        speech_started = False
        speech_stopped = False

        if rms >= self.threshold:
            self._silence_ms = 0
            if not self._in_speech:
                self._in_speech = True
                speech_started = True
        elif self._in_speech:
            self._silence_ms += max(1, chunk_ms)
            if self._silence_ms >= self.silence_timeout_ms:
                self._in_speech = False
                self._silence_ms = 0
                speech_stopped = True

        return AudioProcessorResult(
            chunk=chunk,
            diagnostics={
                "speech_started": speech_started,
                "speech_stopped": speech_stopped,
                "speech_active": self._in_speech,
                "rms": rms,
                "threshold": self.threshold,
                "silence_timeout_ms": self.silence_timeout_ms,
                "diagnostic_only": False,
            },
        )

    @staticmethod
    def _estimate_duration_ms(chunk: StreamChunk) -> int:
        if not chunk.payload or chunk.sample_rate <= 0 or chunk.channels <= 0:
            return 0
        sample_count = len(chunk.payload) // 2 // chunk.channels
        return int(sample_count * 1000 / chunk.sample_rate)


class AudioPipeline:
    """服务器共享音频预处理与路由入口。

    主要功能：只接收 sensor.mic，执行格式校验、重采样、音量诊断和可选本地 VAD，
    再交给当前 Agent Core 或 conversation runtime。该组件仍是新旧链路共享的
    上行音频预处理入口，不属于 legacy-only。
    主要属性：`agent_core` 可以是 legacy realtime pipeline，也可以是新的
    Omni/VL conversation runtime。
    """

    def __init__(
        self,
        *,
        agent_core=None,
        vision_agent_core=None,
        normalizer: FormatNormalizer | None = None,
        config: AudioPipelineConfig | None = None,
        processors: list[AudioProcessor] | None = None,
    ) -> None:
        self.agent_core = agent_core or vision_agent_core
        self.config = config or AudioPipelineConfig()
        self.normalizer = normalizer or FormatNormalizer(self.config)
        self.processors = processors or self._default_processors()
        self.last_diagnostics: list[dict] = []

    def process(self, chunk: StreamChunk) -> None:
        """处理一片麦克风音频。

        主要逻辑：依次执行格式校验、重采样、音量探针和 VAD 处理器；当启用服务端 VAD
        时先把 speech_start/stop 通知 Agent Core，再调用 `append_audio_event()`。
        参数：`chunk` 为 sensor.mic StreamChunk。
        返回值：无。
        异常情况：格式不符合预期或 Agent Core 缺少接口时抛出异常。
        """
        current = chunk
        diagnostics: list[dict] = []
        for processor in self.processors:
            result = processor.process(current)
            current = result.chunk
            diagnostics.append({"processor": processor.name, **dict(result.diagnostics)})
        self.last_diagnostics = diagnostics
        self._emit_vad_boundaries(current, diagnostics)
        self.agent_core.append_audio_event(current)

    def dispatch(self, chunk: StreamChunk) -> None:
        """按 stream_type 分发输入音频。

        主要逻辑：当前只接受 sensor.mic，其他传感器由上层 App 分流到 Asset Service。
        参数：`chunk` 为上行 StreamChunk。
        返回值：无。
        异常情况：非 sensor.mic 时不处理。
        """
        if chunk.stream_type == "sensor.mic":
            self.process(chunk)

    def diagnostics_summary(self) -> dict:
        """返回当前 pipeline 能力摘要。

        主要逻辑：供 preflight 报告组件启用和降级原因。
        参数：无。
        返回值：包含处理器名称、配置和最近诊断的字典。
        异常情况：无。
        """

        return {
            "processors": [processor.name for processor in self.processors],
            "config": {
                "expected_codec": self.config.expected_codec,
                "expected_sample_rate": self.config.expected_sample_rate,
                "expected_channels": self.config.expected_channels,
                "resample": self.config.resample,
                "volume_probe": self.config.volume_probe,
                "vad": self.config.vad,
                "vad_rms_threshold": self.config.vad_rms_threshold,
                "vad_silence_timeout_ms": self.config.vad_silence_timeout_ms,
            },
            "last_diagnostics": list(self.last_diagnostics),
        }

    def _default_processors(self) -> list[AudioProcessor]:
        processors: list[AudioProcessor] = [FormatValidator(self.config), Pcm16Resampler(self.config)]
        if self.config.volume_probe:
            processors.append(VolumeProbe())
        if self.config.vad in {"server", "server_only", "server_vad"}:
            processors.append(
                ServerVadProcessor(
                    threshold=self.config.vad_rms_threshold,
                    silence_timeout_ms=self.config.vad_silence_timeout_ms,
                )
            )
        elif self.config.vad not in {"disabled", "off", "false", "provider"}:
            processors.append(QualityVadProbe(threshold=self.config.vad_rms_threshold))
        return processors

    def _emit_vad_boundaries(self, chunk: StreamChunk, diagnostics: list[dict]) -> None:
        for item in diagnostics:
            if item.get("processor") != "server_vad":
                continue
            if item.get("speech_started") and hasattr(self.agent_core, "on_speech_started"):
                self.agent_core.on_speech_started(
                    chunk.user_id,
                    chunk.session_id,
                    stream_id=chunk.stream_id,
                    reason="server_vad_speech_started",
                    diagnostics=dict(item),
                )
            if item.get("speech_stopped") and hasattr(self.agent_core, "on_speech_stopped"):
                self.agent_core.on_speech_stopped(
                    chunk.user_id,
                    chunk.session_id,
                    stream_id=chunk.stream_id,
                    reason="server_vad_speech_stopped",
                    diagnostics=dict(item),
                )
