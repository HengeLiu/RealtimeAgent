from __future__ import annotations

from dataclasses import dataclass

from audio_chat.agent_core.text import TextAgentCore
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
    def __init__(self, *, text_agent_core: TextAgentCore, normalizer: FormatNormalizer | None = None) -> None:
        self.text_agent_core = text_agent_core
        self.normalizer = normalizer or FormatNormalizer()

    def process(self, chunk: StreamChunk) -> None:
        normalized = self.normalizer.process(chunk)
        self.text_agent_core.append_audio_event(normalized)

    def dispatch(self, chunk: StreamChunk) -> None:
        if chunk.stream_type == "sensor.mic":
            self.process(chunk)
