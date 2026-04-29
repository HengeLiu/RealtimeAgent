"""Agent 长期记忆对象模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agent_core.context.models import generate_id, now_ms

MemoryScope = Literal["device", "session", "user"]
MemorySource = Literal["user_requested", "agent_inferred", "system"]


@dataclass(slots=True)
class AgentMemoryRecord:
    """一条 Agent 长期记忆。

    主要功能：
    1. 保存用户基本信息、偏好和稳定行为习惯。
    2. 记录记忆来源、作用域、更新时间和删除状态，便于审计。

    主要属性：
    1. `memory_id`：记忆唯一编号。
    2. `scope_type/scope_id`：记忆作用域，当前默认按设备隔离。
    3. `text`：可直接注入 Agent 提示词的短句。
    4. `category`：记忆类别，例如 `profile/preference/habit`。
    5. `source`：记忆来源，区分用户主动要求和 Agent 主动推断。
    6. `deleted_at_ms`：删除时间，非空表示软删除。
    """

    memory_id: str
    scope_type: MemoryScope
    scope_id: str
    text: str
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
        text: str,
        category: str = "general",
        source: MemorySource = "agent_inferred",
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> "AgentMemoryRecord":
        """创建一条新记忆。

        参数：
        1. `scope_type/scope_id`：记忆隔离作用域。
        2. `text`：记忆正文，应是短句。
        3. `category/source/confidence/metadata`：记忆辅助信息。

        返回值：
        1. `AgentMemoryRecord`。

        异常情况：
        1. 本方法不做合法性校验，调用方负责在写入前清洗文本。
        """

        return cls(
            memory_id=generate_id("mem"),
            scope_type=scope_type,
            scope_id=scope_id,
            text=text,
            category=category,
            source=source,
            confidence=confidence,
            metadata=metadata or {},
        )

    @property
    def active(self) -> bool:
        """判断记忆是否仍然有效。"""

        return self.deleted_at_ms is None
