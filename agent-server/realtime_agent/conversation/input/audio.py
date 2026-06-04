"""conversation 音频输入边界实现入口。

本模块把现有 `audio_pipeline` 中已经稳定的格式校验、重采样和质量诊断能力包装为
conversation Input Layer 的正式入口。底层复用现有实现，但上层代码应优先从这里
导入，避免继续把音频输入边界理解成旧链路 pipeline。
"""

from realtime_agent.audio_pipeline import AudioPipelineConfig
from realtime_agent.audio_pipeline.service import AudioInputConsumer, AudioPipeline

RuntimeAudioInputBoundary = AudioPipeline

__all__ = [
    "AudioInputConsumer",
    "AudioPipelineConfig",
    "RuntimeAudioInputBoundary",
]
