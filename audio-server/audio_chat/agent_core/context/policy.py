from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ContextPolicy:
    """上下文编译策略。

    主要功能：集中保存历史消息条数、粗略 token 预算、Realtime inline vision 工具过滤等规则。
    第一版只做轻量策略，后续可扩展为配置驱动。
    """

    max_messages: int = 30
    token_budget_total: int = 12000
    token_budget_instructions: int = 3000
    token_budget_messages: int = 5000
    token_budget_memory: int = 1500
    token_budget_tools: int = 2500
    inline_vision_tools: set[str] = field(
        default_factory=lambda: {"capture_photo", "interpret_current_view", "interpret_image"}
    )
    tool_result_default_model_visible: bool = True

    def trim_messages(self, messages: list[dict], *, max_messages: int | None = None) -> list[dict]:
        """按最大条数裁剪 active messages。

        参数：`messages` 为原始消息列表；`max_messages` 可覆盖默认值。
        返回值：保留尾部最近消息。
        异常情况：无。
        """

        limit = max(1, int(max_messages or self.max_messages or 30))
        if len(messages) <= limit:
            return list(messages)
        return list(messages[-limit:])
