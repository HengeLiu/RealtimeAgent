"""语音模型服务内部协议。

该模块只定义 SDK 内部边界，不改变眼镜、手机和业务 Tool 的公开协议。
"""

from __future__ import annotations

from typing import Any, Protocol

from protocol.media import MediaFrame


class VoiceServer(Protocol):
    """Omni Server 与 Text Server 共享的最小控制入口。"""

    def open_session(self, *, device_id: str, device_type: str, session_id: str) -> None: ...

    def on_voice_session_opened(self, *, device_id: str, session_id: str) -> None: ...

    def on_segment_started(self, *, device_id: str, session_id: str, payload: dict[str, Any]) -> None: ...

    def on_audio_frame(self, *, device_id: str, frame: MediaFrame) -> None: ...

    def on_segment_finished(self, *, device_id: str, session_id: str, payload: dict[str, Any]) -> None: ...

    def submit_notification(self, **kwargs: Any) -> dict[str, Any]: ...

    def build_runtime_snapshot(self) -> dict[str, dict[str, Any]]: ...
