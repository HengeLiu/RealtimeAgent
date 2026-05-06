from __future__ import annotations

from audio_chat.agent_core.realtime import RealtimeAudioAgentCore
from audio_chat.agent_core.text import TextAgentCore


class AgentCoreRouter:
    """Agent Core 路由器。

    主要功能：根据 `agent.mode` 选择 Agent Core 实现。
    主要方法：`build()`。
    """

    @staticmethod
    def build(*, mode: str, **kwargs):
        """创建 Agent Core。

        主要逻辑：`text` 返回 `TextAgentCore`，`realtime_audio` 返回
        `RealtimeAudioAgentCore`；`auto` 第一版保守落到 text；`custom` 明确失败。
        参数：`mode` 为 YAML 中的 agent.mode，其余参数透传给具体 Agent Core。
        返回值：Agent Core 实例。
        异常情况：未知模式或未实现模式抛出异常。
        """
        if mode == "text":
            return TextAgentCore(**_text_kwargs(kwargs))
        if mode == "realtime_audio":
            return RealtimeAudioAgentCore(**kwargs)
        if mode == "auto":
            return TextAgentCore(**_text_kwargs(kwargs))
        if mode == "custom":
            raise NotImplementedError("agent.mode=custom requires an app-module custom core factory")
        raise ValueError(f"unsupported agent.mode: {mode}")


def _text_kwargs(kwargs: dict) -> dict:
    return {
        key: value
        for key, value in kwargs.items()
        if key in {"control_service", "output_service", "recorder", "asr_config", "text_model_config"}
    }
