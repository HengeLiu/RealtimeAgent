from __future__ import annotations

from dataclasses import dataclass

from audio_chat.protocol import StreamChunk


@dataclass(frozen=True)
class AudioPipelineConfig:
    expected_codec: str = "pcm16le"
    expected_sample_rate: int = 16000
    expected_channels: int = 1


class FormatNormalizer:
    def __init__(self, config: AudioPipelineConfig | None = None) -> None:
        self.config = config or AudioPipelineConfig()

    def process(self, chunk: StreamChunk) -> StreamChunk:
        if chunk.stream_type != "sensor.mic":
            raise ValueError("Audio Pipeline only accepts sensor.mic")
        if chunk.codec != self.config.expected_codec:
            raise ValueError("unsupported sensor.mic codec")
        if chunk.sample_rate != self.config.expected_sample_rate:
            raise ValueError("unsupported sensor.mic sample_rate")
        if chunk.channels != self.config.expected_channels:
            raise ValueError("unsupported sensor.mic channels")
        return chunk


class AudioPipeline:
    """服务器音频预处理与路由。

    主要功能：只接收 sensor.mic，做最小格式校验后交给当前 Agent Core。
    主要属性：`agent_core` 可以是 TextAgentCore，也可以是 RealtimeAudioAgentCore。
    """

    def __init__(self, *, agent_core=None, text_agent_core=None, normalizer: FormatNormalizer | None = None) -> None:
        self.agent_core = agent_core or text_agent_core
        self.normalizer = normalizer or FormatNormalizer()

    def process(self, chunk: StreamChunk) -> None:
        """处理一片麦克风音频。

        主要逻辑：先执行 `FormatNormalizer`，再调用 Agent Core 的
        `append_audio_event()`；turn boundary 不在 Audio Pipeline 内判断。
        参数：`chunk` 为 sensor.mic StreamChunk。
        返回值：无。
        异常情况：格式不符合预期或 Agent Core 缺少接口时抛出异常。
        """
        normalized = self.normalizer.process(chunk)
        self.agent_core.append_audio_event(normalized)

    def dispatch(self, chunk: StreamChunk) -> None:
        """按 stream_type 分发输入音频。

        主要逻辑：当前只接受 sensor.mic，其他传感器由上层 App 分流到 Asset Service。
        参数：`chunk` 为上行 StreamChunk。
        返回值：无。
        异常情况：非 sensor.mic 时不处理。
        """
        if chunk.stream_type == "sensor.mic":
            self.process(chunk)
