"""语音运行时共享数据模型。"""

from __future__ import annotations

from dataclasses import dataclass

from runtime.voice_constants import MODEL_OUTPUT_SAMPLE_RATE_HZ


@dataclass(slots=True)
class ModelChunk:
    """模型流式结果分片。"""

    text_delta: str = ""
    audio_pcm_bytes: bytes = b""
    sample_rate_hz: int = MODEL_OUTPUT_SAMPLE_RATE_HZ
