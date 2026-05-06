from __future__ import annotations

from typing import Protocol


class RealtimeProviderAdapter(Protocol):
    """Realtime audio provider adapter 接口。

    主要功能：为后续 Omni / RealtimeAudioAgentCore 预留 provider 边界，避免把 realtime
    逻辑塞进 Audio Pipeline 或 TextAgentCore。
    主要方法：后续阶段补充会话打开、音频输入、音频输出和取消接口。
    """


class RealtimeAudioAgentCore:
    """Realtime audio agent core 占位实现。

    主要功能：明确当前阶段尚未实现 realtime audio agent，避免配置误用时静默退回文本链路。
    主要方法：构造函数直接抛出 `NotImplementedError`。
    """

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError("agent.mode=realtime_audio is not implemented in this phase")
