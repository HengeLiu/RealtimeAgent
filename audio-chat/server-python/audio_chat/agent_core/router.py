from __future__ import annotations

from audio_chat.agent_core.realtime import RealtimeAudioAgentCore
from audio_chat.agent_core.text import TextAgentCore


class AgentCoreRouter:
    """Agent Core 路由器。

    主要功能：根据 `agent.mode` 选择 Agent Core 实现；当前只允许 `text`，对
    `realtime_audio` 给出明确未实现错误。
    主要方法：`build()`。
    """

    @staticmethod
    def build(*, mode: str, **kwargs):
        """创建 Agent Core。

        主要逻辑：`text` 返回 `TextAgentCore`，`realtime_audio` 抛出明确错误。
        参数：`mode` 为 YAML 中的 agent.mode，其余参数透传给具体 Agent Core。
        返回值：Agent Core 实例。
        异常情况：未知模式或未实现模式抛出异常。
        """
        if mode == "text":
            return TextAgentCore(**kwargs)
        if mode == "realtime_audio":
            return RealtimeAudioAgentCore(**kwargs)
        raise ValueError(f"unsupported agent.mode: {mode}")
