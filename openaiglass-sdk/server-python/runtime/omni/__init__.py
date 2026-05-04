"""Omni Realtime 运行时边界。

当前阶段先暴露 Omni 专用类型入口，便于后续把 `VoiceRuntime` 中的 DashScope
Realtime 热路径整体迁入本包。
"""

from runtime.voice_runtime import (  # noqa: F401
    DashscopeOmniRealtimeReplyClient,
    OmniRealtimeReplyResult,
    OmniRealtimeStreamingSession,
)
