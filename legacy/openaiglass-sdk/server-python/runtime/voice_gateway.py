"""语音服务网关。

第一阶段网关保持行为不变：现有 `VoiceRuntime` 仍承载热路径，配置层先明确
`omni_server` / `text_server` 的模型服务边界。后续阶段会把具体实现迁入
`runtime.omni` 与 `runtime.text`。
"""

from __future__ import annotations

from dataclasses import dataclass

from infra.config import ServerSettings
from infra.errors import ErrorCode, build_error
from runtime.omni.omni_voice_server import OmniVoiceServer
from runtime.text.text_voice_server import TextVoiceServer
from runtime.voice_runtime import VoiceRuntime
from runtime.voice_server_base import VoiceServer


@dataclass(slots=True)
class VoiceGateway:
    """根据 `voice_server_mode` 选择语音模型服务。"""

    settings: ServerSettings
    server: VoiceServer

    def selected_server(self) -> VoiceServer:
        """返回当前配置选中的语音服务实现。"""

        voice_server_mode = self.settings.effective_voice_server_mode()
        if voice_server_mode not in {"omni_server", "text_server"}:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_SERVER_MODE 非法",
                details={"voice_server_mode": voice_server_mode},
            )
        return self.server

    @classmethod
    def from_runtime(cls, *, settings: ServerSettings, runtime: VoiceRuntime) -> "VoiceGateway":
        """按配置把现有 `VoiceRuntime` 包装成具体 VoiceServer。"""

        voice_server_mode = settings.effective_voice_server_mode()
        if voice_server_mode == "omni_server":
            return cls(settings=settings, server=OmniVoiceServer(settings=settings, runtime=runtime))
        if voice_server_mode == "text_server":
            return cls(settings=settings, server=TextVoiceServer(settings=settings, runtime=runtime))
        raise build_error(
            ErrorCode.INVALID_CONFIG,
            "VOICE_SERVER_MODE 非法",
            details={"voice_server_mode": voice_server_mode},
        )
