from __future__ import annotations

from audio_chat.app import AudioChatApp, AudioChatConfig


def create_app(config: AudioChatConfig | None = None) -> AudioChatApp:
    """创建测试用 basic app。"""

    return AudioChatApp(config or AudioChatConfig())
