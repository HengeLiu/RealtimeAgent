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

        主要逻辑：先保留尾部最近消息，再移除缺少配套 assistant tool_call 的
        孤立 tool result，避免 provider 拒绝非法工具历史。
        参数：`messages` 为原始消息列表；`max_messages` 可覆盖默认值。
        返回值：保留尾部最近且协议合法的消息。
        异常情况：无。
        """

        limit = max(1, int(max_messages or self.max_messages or 30))
        if len(messages) <= limit:
            return _drop_orphan_tool_messages(list(messages))
        return _drop_orphan_tool_messages(list(messages[-limit:]))


def _drop_orphan_tool_messages(messages: list[dict]) -> list[dict]:
    """移除没有对应 assistant tool_call 的 tool 消息。

    主要逻辑：从当前窗口内扫描 assistant.tool_calls，只有 id 能匹配上的 tool
    result 才保留。这样按窗口裁剪后不会留下孤立工具结果。
    参数：`messages` 为裁剪后的候选消息。
    返回值：协议上可回放的消息列表。
    异常情况：无。
    """

    available_tool_call_ids: set[str] = set()
    for message in messages:
        if message.get("role") != "assistant":
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for item in tool_calls:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("id")
            if raw_id:
                available_tool_call_ids.add(str(raw_id))
    if not available_tool_call_ids:
        return [message for message in messages if message.get("role") != "tool"]
    return [
        message
        for message in messages
        if message.get("role") != "tool" or str(message.get("tool_call_id") or "") in available_tool_call_ids
    ]
