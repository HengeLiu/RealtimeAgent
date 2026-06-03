from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from realtime_agent.conversation.types import SpeechInputDelta
from realtime_agent.protocol import StreamChunk


class AudioInputBoundary(Protocol):
    """音频输入边界抽象。

    主要功能：校验并规范化端侧上传的原始音频，完成必要的重采样、声道转换和
    轻量质量诊断，再把规范化后的 `StreamChunk` 交给 `SpeechInputBoundary`。
    """

    def normalize(self, chunk: StreamChunk) -> StreamChunk:
        """规范化一片上行音频。

        参数：`chunk` 为端侧上传的原始音频片。
        返回值：符合后续语音边界要求的音频片。
        异常情况：音频格式不合法或无法转换时抛出实现侧异常。
        """


class SpeechInputBoundary(Protocol):
    """语音输入边界抽象。

    主要功能：把规范化后的连续麦克风音频转换为 `SpeechInputDelta`，屏蔽服务端
    VAD、ASR/VAD 合一 provider 和未来端侧提交按钮等来源差异。
    """

    def append_audio(self, chunk: StreamChunk) -> Iterator[SpeechInputDelta]:
        """追加一片音频并返回本片触发的输入增量。"""

    def flush(self) -> Iterator[SpeechInputDelta]:
        """刷新边界内部缓存并返回剩余输入增量。"""
