"""Omni Server 适配层。

该类把 `VoiceRuntime` 中已经稳定的 Omni 热路径包装成独立 server 边界。
后续迁移 DashScope Realtime 细节时，控制层只依赖这个边界。
"""

from __future__ import annotations

from typing import Any

from infra.config import ServerSettings
from infra.errors import ErrorCode, build_error
from protocol.media import MediaFrame
from runtime.voice_runtime import VoiceRuntime


class OmniVoiceServer:
    """Omni Realtime 模型服务适配器。"""

    mode = "omni_server"

    def __init__(self, *, settings: ServerSettings, runtime: VoiceRuntime) -> None:
        self._settings = settings
        self._runtime = runtime
        if settings.effective_voice_server_mode() != self.mode:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "OmniVoiceServer 只能在 VOICE_SERVER_MODE=omni_server 时使用",
                details={"voice_server_mode": settings.effective_voice_server_mode()},
            )

    @property
    def runtime(self) -> VoiceRuntime:
        return self._runtime

    def open_session(self, *, device_id: str, device_type: str, session_id: str) -> None:
        self._runtime.open_session(device_id=device_id, device_type=device_type, session_id=session_id)

    def on_voice_session_opened(self, *, device_id: str, session_id: str) -> None:
        self._runtime.on_voice_session_opened(device_id=device_id, session_id=session_id)

    def on_segment_started(self, *, device_id: str, session_id: str, payload: dict[str, Any]) -> None:
        self._runtime.on_segment_started(device_id=device_id, session_id=session_id, payload=payload)

    def on_audio_frame(self, *, device_id: str, frame: MediaFrame) -> None:
        self._runtime.on_audio_frame(device_id=device_id, frame=frame)

    def on_segment_finished(self, *, device_id: str, session_id: str, payload: dict[str, Any]) -> None:
        self._runtime.on_segment_finished(device_id=device_id, session_id=session_id, payload=payload)

    def submit_notification(self, **kwargs: Any) -> dict[str, Any]:
        return self._runtime.submit_notification(**kwargs)

    def build_runtime_snapshot(self) -> dict[str, dict[str, Any]]:
        return self._runtime.build_runtime_snapshot()
