from __future__ import annotations

from typing import Protocol

from realtime_agent.protocol import StreamChunk


class SpeakerSinkABC(Protocol):
    """扬声器输出端抽象。

    主要功能：接收 OutputService 仲裁后的下行音频 chunk，并写入端侧 speaker
    stream。该抽象不构造 prompt，不执行工具，也不判断用户是否说话。
    """

    def write_audio(self, chunk: StreamChunk) -> None:
        """写入一片下行音频。"""

    def finish(self, *, reason: str) -> None:
        """通知下行音频输出结束。"""

    def cancel(self, *, reason: str) -> None:
        """取消当前下行音频输出。"""
