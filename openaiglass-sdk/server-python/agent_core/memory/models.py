"""Agent 长期记忆对象模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agent_core.context.models import generate_id, now_ms

MemoryScope = Literal["device", "session", "user"]
MemorySource = Literal["user_requested", "agent_inferred", "system"]
MemoryType = Literal["basic", "personalized"]


@dataclass(slots=True)
class AgentMemoryRecord:
    """一条 Agent 长期记忆。

    主要功能：
    1. 保存基本信息和个性化信息。
    2. 基本信息保存短小、稳定、每轮直接注入的信息。
    3. 个性化信息保存主题和详细内容，每轮只注入主题，详情由 `memory_search` 按需读取。
    4. 记录作用域和更新时间，便于审计。

    主要属性：
    1. `memory_id`：记忆唯一编号。
    2. `scope_type/scope_id`：记忆作用域，当前默认按设备隔离。
    3. `memory_type`：记忆类型，`basic` 表示基本信息，`personalized` 表示个性化信息。
    4. `topic`：记忆主题；个性化信息必须有主题。
    5. `content`：记忆正文。
    6. `source`：记忆来源，区分用户主动要求和 Agent 主动推断。
    """

    memory_id: str
    scope_type: MemoryScope
    scope_id: str
    memory_type: MemoryType
    topic: str
    content: str
    source: MemorySource = "agent_inferred"
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at_ms: int = field(default_factory=now_ms)
    updated_at_ms: int = field(default_factory=now_ms)

    @classmethod
    def create(
        cls,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        memory_type: MemoryType,
        content: str,
        topic: str = "",
        source: MemorySource = "agent_inferred",
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> "AgentMemoryRecord":
        """创建一条新记忆。

        参数：
        1. `scope_type/scope_id`：记忆隔离作用域。
        2. `memory_type`：基本信息或个性化信息。
        3. `topic/content`：记忆主题和正文。
        4. `source/confidence/metadata`：记忆辅助信息。

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
            topic=topic,
            content=content,
            source=source,
            confidence=confidence,
            metadata=metadata or {},
        )
