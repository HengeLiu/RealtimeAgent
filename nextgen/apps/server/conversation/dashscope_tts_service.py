"""阿里百炼 TTS 服务封装。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from dashscope.audio.tts import ResultCallback, SpeechSynthesizer


class _StreamingTtsCallback(ResultCallback):
    """流式 TTS 回调。"""

    def __init__(self, on_audio_chunk: Callable[[bytes], None]) -> None:
        self.on_audio_chunk = on_audio_chunk

    def on_open(self):
        pass

    def on_complete(self):
        pass

    def on_error(self, _response):
        pass

    def on_close(self):
        pass

    def on_event(self, result):
        frame = result.get_audio_frame()
        if frame:
            self.on_audio_chunk(frame)


@dataclass
class DashscopeTtsService:
    """阿里百炼 TTS 服务。"""

    api_key: str | None = None
    model: str = "qwen-tts"
    voice: str = "longxiaochun"
    sample_rate: int = 16000
    audio_format: str = "pcm"

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.getenv("DASHSCOPE_API_KEY")

    def stream_text(self, text: str, on_audio_chunk: Callable[[bytes], None]) -> bytes:
        """把文本转为流式音频，并在回调中返回每段音频。"""

        if not self.api_key:
            raise RuntimeError("未配置 DASHSCOPE_API_KEY，无法调用百炼 TTS。")
        callback = _StreamingTtsCallback(on_audio_chunk=on_audio_chunk)
        result = SpeechSynthesizer.call(
            model=self.model,
            text=text,
            callback=callback,
            api_key=self.api_key,
            voice=self.voice,
            format=self.audio_format,
            sample_rate=self.sample_rate,
        )
        return result.get_audio_data() or b""
