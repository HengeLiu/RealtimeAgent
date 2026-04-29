"""Agent 长期记忆运行时。"""

from __future__ import annotations

from dataclasses import asdict

from agent_core.memory.models import AgentMemoryRecord, MemoryScope, MemorySource
from agent_core.memory.store import AgentMemoryStore, InMemoryAgentMemoryStore


class AgentMemoryRuntime:
    """Agent 长期记忆运行时。

    主要功能：
    1. 为 Agent Loop 提供可注入提示词的相关记忆。
    2. 为模型可见 Tool 提供新增、查询、删除记忆的统一入口。
    3. 隔离存储实现，后续可替换为 Mem0、Zep、Graphiti 或向量库。

    主要方法：
    1. `add_memory`：新增一条记忆。
    2. `search_memories`：按关键词检索记忆。
    3. `delete_memory`：按编号软删除记忆。
    4. `build_prompt_fragment`：构造系统提示词片段。
    """

    def __init__(
        self,
        *,
        store: AgentMemoryStore | None = None,
        enabled: bool = True,
        max_prompt_memories: int = 6,
    ) -> None:
        self._store = store or InMemoryAgentMemoryStore()
        self.enabled = enabled
        self.max_prompt_memories = max(0, max_prompt_memories)

    def add_memory(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        text: str,
        category: str = "general",
        source: MemorySource = "agent_inferred",
        confidence: float = 1.0,
        metadata: dict | None = None,
    ) -> AgentMemoryRecord:
        """新增一条长期记忆。

        参数：
        1. `scope_type/scope_id`：记忆作用域。
        2. `text`：记忆正文。
        3. `category/source/confidence/metadata`：辅助信息。

        返回值：
        1. 已写入的 `AgentMemoryRecord`。

        异常情况：
        1. 记忆关闭、作用域为空或正文为空时抛出 `ValueError`。
        """

        if not self.enabled:
            raise ValueError("Agent 记忆功能未启用")
        normalized_text = self._normalize_text(text)
        if not normalized_text:
            raise ValueError("记忆内容不能为空")
        normalized_scope_id = scope_id.strip()
        if not normalized_scope_id:
            raise ValueError("记忆作用域不能为空")
        record = AgentMemoryRecord.create(
            scope_type=scope_type,
            scope_id=normalized_scope_id,
            text=normalized_text,
            category=category.strip() or "general",
            source=source,
            confidence=max(0.0, min(1.0, confidence)),
            metadata=metadata or {},
        )
        return self._store.upsert(record)

    def search_memories(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        query: str = "",
        limit: int = 5,
    ) -> list[AgentMemoryRecord]:
        """查询长期记忆。"""

        if not self.enabled:
            return []
        normalized_scope_id = scope_id.strip()
        if not normalized_scope_id:
            return []
        return self._store.search(
            scope_type=scope_type,
            scope_id=normalized_scope_id,
            query=query,
            limit=max(1, limit),
        )

    def list_memories(self, *, scope_type: MemoryScope, scope_id: str, limit: int = 20) -> list[AgentMemoryRecord]:
        """列出当前作用域下的长期记忆。"""

        if not self.enabled:
            return []
        return self._store.list_active(scope_type=scope_type, scope_id=scope_id.strip())[: max(1, limit)]

    def delete_memory(self, *, memory_id: str, scope_type: MemoryScope, scope_id: str) -> AgentMemoryRecord | None:
        """软删除一条长期记忆。"""

        if not self.enabled:
            raise ValueError("Agent 记忆功能未启用")
        normalized_memory_id = memory_id.strip()
        if not normalized_memory_id:
            raise ValueError("memory_id 不能为空")
        return self._store.delete(
            memory_id=normalized_memory_id,
            scope_type=scope_type,
            scope_id=scope_id.strip(),
        )

    def build_prompt_fragment(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        query: str,
    ) -> str:
        """构造可注入系统提示词的记忆片段。

        参数：
        1. `scope_type/scope_id`：记忆作用域。
        2. `query`：当前用户输入，用于筛选相关记忆。

        返回值：
        1. 系统提示词片段；无记忆时返回空字符串。
        """

        if not self.enabled or self.max_prompt_memories <= 0:
            return ""
        records = self.search_memories(
            scope_type=scope_type,
            scope_id=scope_id,
            query=query,
            limit=self.max_prompt_memories,
        )
        if not records:
            records = self.list_memories(
                scope_type=scope_type,
                scope_id=scope_id,
                limit=self.max_prompt_memories,
            )
        if not records:
            return ""
        lines = [
            "以下是系统长期记忆，只能用于改善当前回答，不要逐字复述；如果用户要求删除或修改记忆，请调用 manage_memory 工具处理："
        ]
        for index, record in enumerate(records, start=1):
            lines.append(f"{index}. [{record.memory_id}] {record.text}")
        return "\n".join(lines)

    @staticmethod
    def record_to_dict(record: AgentMemoryRecord) -> dict:
        """把记忆对象转换成可返回给 Tool 的字典。"""

        return asdict(record)

    @staticmethod
    def _normalize_text(text: str) -> str:
        """清洗记忆正文，避免把长对话直接存成记忆。"""

        return " ".join(text.strip().split())[:500]
