from __future__ import annotations

from typing import Any, Mapping, Protocol

from realtime_agent.protocol import StreamChunk


class VLMProviderABC(Protocol):
    """视觉语言模型 provider 抽象。

    主要功能：接收 messages、tools 和视觉 content blocks，输出文本增量、tool call
    或最终文本结果。Provider 不保存 Agent 记忆，也不直接执行工具。
    """

    provider_name: str
    model: str

    def generate(self, *, messages: list[Mapping[str, Any]], tools: list[Mapping[str, Any]]) -> Any:
        """执行一次 VLM 请求。"""

    def stream_messages(self, *, messages: list[Mapping[str, Any]], tools: list[Mapping[str, Any]]) -> Any:
        """流式执行一次 VLM 请求，返回 text delta 或 tool call。"""


class OmniRealtimeProviderABC(Protocol):
    """Omni realtime provider 抽象。

    主要功能：接收音频片、图片输入、manual commit 和 response create 请求，输出
    provider realtime 事件。
    """

    provider_name: str
    model: str

    def open(self, *, user_id: str, session_id: str, callbacks: Any) -> None:
        """打开 realtime provider 会话。"""

    def append_audio(self, chunk: StreamChunk) -> None:
        """追加一片用户音频。"""

    def append_image(self, image: bytes, *, user_id: str, session_id: str, metadata: dict[str, Any] | None = None) -> None:
        """追加一帧视觉输入。"""

    def commit_input(self, *, user_id: str, session_id: str, reason: str) -> None:
        """提交当前输入 buffer。"""

    def create_response(self, *, user_id: str, session_id: str, reason: str, instructions: str | None = None) -> None:
        """创建一次 realtime 响应。"""

    def cancel(self, *, user_id: str, reason: str) -> None:
        """取消当前响应。"""

    def close(self, *, user_id: str, reason: str) -> None:
        """关闭 realtime provider 会话。"""


class ASRProviderABC(Protocol):
    """ASR provider 抽象。

    主要功能：接收音频片并输出 ASR 文本增量、final text，以及可选 speech boundary
    元数据。
    """

    provider_name: str
    model: str

    def append_audio(self, chunk: StreamChunk) -> list[Any]:
        """追加一片音频并返回 provider 事件。"""

    def cancel(self) -> None:
        """取消当前 ASR 会话。"""

    def close(self) -> None:
        """关闭当前 ASR 会话。"""


class TTSProviderABC(Protocol):
    """TTS provider 抽象。

    主要功能：接收文本并输出可播放音频，不保存 Agent 上下文。
    """

    provider_name: str
    model: str

    def synthesize_text(self, text: str) -> bytes:
        """合成完整文本。"""

    def stream_synthesize(self, text: str) -> Any:
        """流式合成文本，返回音频 chunk。"""
