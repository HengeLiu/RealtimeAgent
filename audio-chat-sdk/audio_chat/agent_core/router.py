from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from audio_chat.agent_core.base import AgentCore
from audio_chat.agent_core.realtime import RealtimeAudioAgentCore
from audio_chat.agent_core.text import TextAgentCore


@dataclass
class AgentCoreRouter:
    """Agent Core 路由器。

    主要功能：
    1. 根据 `agent.mode` 选择内置 Agent Core 实现。
    2. 允许业务或测试注册 custom factory，而不改 SDK 内部 core。

    主要方法：
    1. `register_factory()`：注册自定义 core 工厂。
    2. `create()`：按 mode 创建 core。
    3. `build()`：历史静态入口。
    """

    factories: dict[str, Callable[..., AgentCore]] = field(default_factory=dict)

    def register_factory(self, mode: str, factory: Callable[..., AgentCore]) -> None:
        """注册自定义 Agent Core 工厂。

        参数：
        1. `mode`：自定义模式名，例如 `custom` 或 `vision_realtime`。
        2. `factory`：接收依赖并返回 AgentCore 的工厂。

        返回值：无。
        异常情况：模式名为空或重复注册内置模式时抛出异常。
        """

        normalized = mode.strip()
        if not normalized:
            raise ValueError("agent core mode 不能为空")
        if normalized in {"text", "realtime_audio", "auto"}:
            raise ValueError(f"不能覆盖内置 agent mode: {normalized}")
        self.factories[normalized] = factory

    def create(self, *, mode: str, **kwargs) -> AgentCore:
        """创建 Agent Core。

        主要逻辑：`text` 返回 `TextAgentCore`，`realtime_audio` 返回
        `RealtimeAudioAgentCore`；`auto` 第一版保守落到 text；其他模式查找
        已注册自定义工厂。
        参数：`mode` 为 YAML 中的 agent.mode，其余参数透传给具体 Agent Core。
        返回值：Agent Core 实例。
        异常情况：未知模式抛出异常。
        """

        normalized = _normalize_mode(mode)
        if normalized == "text":
            return TextAgentCore(**_text_kwargs(kwargs))
        if normalized == "realtime_audio":
            return RealtimeAudioAgentCore(**kwargs)
        if normalized == "auto":
            return TextAgentCore(**_text_kwargs(kwargs))
        factory = self.factories.get(normalized)
        if factory is not None:
            return factory(**kwargs)
        raise ValueError(f"unsupported agent.mode: {mode}")

    @staticmethod
    def build(*, mode: str, custom_factories: dict[str, Callable[..., AgentCore]] | None = None, **kwargs) -> AgentCore:
        """历史调用方式的静态构建入口。"""

        router = AgentCoreRouter()
        for name, factory in dict(custom_factories or {}).items():
            router.register_factory(name, factory)
        if mode == "custom" and not custom_factories:
            raise NotImplementedError("agent.mode=custom requires an app-module custom core factory")
        return router.create(mode=mode, **kwargs)


def _text_kwargs(kwargs: dict) -> dict:
    return {
        key: value
        for key, value in kwargs.items()
        if key in {"control_service", "output_service", "recorder", "asr_config", "text_model_config", "tool_gateway"}
        or key == "memory_service"
    }


def _normalize_mode(mode: str) -> str:
    """规范化 Agent Core 模式别名。"""

    normalized = str(mode or "text").strip().lower()
    if normalized in {"realtime", "omni", "omni_realtime"}:
        return "realtime_audio"
    return normalized
