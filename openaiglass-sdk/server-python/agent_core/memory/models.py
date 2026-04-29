"""Agent 长期记忆对象模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agent_core.context.models import generate_id, now_ms

MemoryScope = Literal["device", "session", "user"]
MemorySource = Literal["user_requested", "agent_inferred", "system"]
MemoryType = Literal["hot", "cold"]


@dataclass(slots=True)
class AgentMemoryRecord:
    """一条 Agent 长期记忆。

    主要功能：
    1. 保存热记忆和冷记忆。
    2. 热记忆保存短小、稳定、每轮直接注入的信息。
    3. 冷记忆保存标题和详细内容，每轮只注入标题，详情由 `memory_search` 按需读取。
    4. 记录记忆来源、作用域、更新时间和删除状态，便于审计。

    主要属性：
    1. `memory_id`：记忆唯一编号。
    2. `scope_type/scope_id`：记忆作用域，当前默认按设备隔离。
    3. `memory_type`：记忆类型，`hot` 表示热记忆，`cold` 表示冷记忆。
    4. `title`：记忆标题；冷记忆必须有标题。
    5. `content`：记忆正文。
    6. `category`：记忆类别，例如 `profile/preference/habit`。
    7. `source`：记忆来源，区分用户主动要求和 Agent 主动推断。
    8. `deleted_at_ms`：删除时间，非空表示软删除。
    """

    memory_id: str
    scope_type: MemoryScope
    scope_id: str
    memory_type: MemoryType
    title: str
    content: str
    category: str = "general"
    source: MemorySource = "agent_inferred"
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at_ms: int = field(default_factory=now_ms)
    updated_at_ms: int = field(default_factory=now_ms)
    deleted_at_ms: int | None = None

    @classmethod
    def create(
        cls,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        memory_type: MemoryType,
        title: str,
        content: str,
        category: str = "general",
        source: MemorySource = "agent_inferred",
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> "AgentMemoryRecord":
        """创建一条新记忆。

        参数：
        1. `scope_type/scope_id`：记忆隔离作用域。
        2. `memory_type`：热记忆或冷记忆。
        3. `title/content`：记忆标题和正文。
        4. `category/source/confidence/metadata`：记忆辅助信息。

        返回值：
        1. `AgentMemoryRecord`。

        异常情况：
        1. 本方法不做合法性校验，调用方负责在写入前清洗文本。
        """

        return cls(
            memory_id=generate_id("mem"),
            scope_type=scope_type,
            scope_id=scope_id,
            memory_type=memory_type,
            title=title,
            content=content,
            category=category,
            source=source,
            confidence=confidence,
            metadata=metadata or {},
        )

    @property
    def active(self) -> bool:
        """判断记忆是否仍然有效。"""

        return self.deleted_at_ms is None

    @property
    def text(self) -> str:
        """兼容旧版本统一记忆字段。"""

        return self.content
