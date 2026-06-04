from __future__ import annotations

from typing import Any

from realtime_agent.conversation.context.models import ContextSource, estimate_tokens


def make_source(
    *,
    source_id: str,
    source_kind: str,
    source_name: str,
    content: Any,
    priority: int = 100,
    included: bool = True,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> ContextSource:
    """创建带 token 估算的 ContextSource。

    主要功能：减少 compiler 中重复的来源记录构造代码。
    参数：`source_id/source_kind/source_name` 描述来源，`content` 为模型可见内容或摘要。
    返回值：ContextSource。
    异常情况：无。
    """

    return ContextSource(
        source_id=source_id,
        source_kind=source_kind,  # type: ignore[arg-type]
        source_name=source_name,
        content=content,
        token_estimate=estimate_tokens(content),
        priority=priority,
        included=included,
        reason=reason,
        metadata=dict(metadata or {}),
    )
