"""Agent 长期记忆运行时。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from agent_core.memory.models import AgentMemoryRecord, MemoryScope, MemorySource, MemoryType
from agent_core.memory.store import AgentMemoryStore, InMemoryAgentMemoryStore

MemoryOperation = Literal["add", "update", "delete"]


@dataclass(slots=True)
class MemoryOperationRequest:
    """记忆管理请求。

    主要功能：
    1. 承载主 Agent 对记忆管理子 Agent 的请求。
    2. 保留用户原始指令，避免主 Agent 过早决定冷热分类和具体字段。
    """

    operation: MemoryOperation
    query: str
    preferred_memory_type: MemoryType | None = None
    title: str = ""
    content: str = ""
    memory_id: str = ""
    category: str = "general"
    source: MemorySource = "user_requested"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryOperationPlan:
    """记忆管理子 Agent 输出的结构化计划。"""

    operation: MemoryOperation
    memory_type: MemoryType
    title: str
    content: str = ""
    memory_id: str = ""
    category: str = "general"
    reason: str = ""


class MemoryManagementAgent:
    """记忆管理子 Agent 接口。

    主要功能：
    1. 根据已有记忆、期望操作和用户原始指令决定冷热分类。
    2. 输出结构化操作计划，真正落盘仍由 SDK 运行时执行。
    """

    def plan(
        self,
        *,
        request: MemoryOperationRequest,
        existing_memories: list[AgentMemoryRecord],
    ) -> MemoryOperationPlan:
        """生成记忆操作计划。"""

        raise NotImplementedError


class HeuristicMemoryManagementAgent(MemoryManagementAgent):
    """本地确定性记忆管理 Agent。

    主要功能：
    1. 为单元测试、离线开发和模型不可用场景提供稳定 fallback。
    2. 按标题、正文长度和操作参数推断冷热记忆。
    """

    _HOT_TITLES = {"姓名", "名字", "年龄", "性别", "生日", "语言", "称呼"}

    def plan(
        self,
        *,
        request: MemoryOperationRequest,
        existing_memories: list[AgentMemoryRecord],
    ) -> MemoryOperationPlan:
        """生成确定性记忆操作计划。"""

        title = request.title.strip() or self._infer_title(request.query)
        content = request.content.strip() or request.query.strip()
        memory_type = request.preferred_memory_type or self._infer_memory_type(title=title, content=content)
        if request.operation == "delete" and not title and request.memory_id:
            matched = next((item for item in existing_memories if item.memory_id == request.memory_id), None)
            if matched is not None:
                title = matched.title
                memory_type = matched.memory_type
        return MemoryOperationPlan(
            operation=request.operation,
            memory_type=memory_type,
            title=title,
            content=content,
            memory_id=request.memory_id.strip(),
            category=request.category.strip() or "general",
            reason="heuristic",
        )

    def _infer_memory_type(self, *, title: str, content: str) -> MemoryType:
        if title in self._HOT_TITLES and len(content) <= 80:
            return "hot"
        return "hot" if len(content) <= 40 and title in self._HOT_TITLES else "cold"

    @staticmethod
    def _infer_title(query: str) -> str:
        text = " ".join(query.strip().replace("：", ":").split())
        if ":" in text:
            return text.split(":", 1)[0].strip()[:30] or "未命名记忆"
        for marker in ("是", "为", "叫"):
            if marker in text and len(text.split(marker, 1)[0]) <= 12:
                return text.split(marker, 1)[0].strip()[:30] or "未命名记忆"
        return text[:20] or "未命名记忆"


class LlmMemoryManagementAgent(MemoryManagementAgent):
    """基于大模型的记忆管理子 Agent。

    主要功能：
    1. 使用主服务端模型把用户原始指令转换成结构化记忆操作计划。
    2. 模型不可用或返回异常时，退回确定性 fallback，避免阻塞主链路。
    """

    def __init__(self, *, settings, fallback: MemoryManagementAgent | None = None) -> None:
        self._settings = settings
        self._fallback = fallback or HeuristicMemoryManagementAgent()

    def plan(
        self,
        *,
        request: MemoryOperationRequest,
        existing_memories: list[AgentMemoryRecord],
    ) -> MemoryOperationPlan:
        """调用大模型生成记忆操作计划。"""

        if not getattr(self._settings, "dashscope_api_key", "").strip():
            return self._fallback.plan(request=request, existing_memories=existing_memories)
        try:
            from openai import OpenAI
        except ImportError:
            return self._fallback.plan(request=request, existing_memories=existing_memories)

        try:
            client = OpenAI(
                api_key=self._settings.dashscope_api_key,
                base_url=self._settings.voice_model_base_url.rstrip("/"),
            )
            completion = client.chat.completions.create(
                model=self._settings.agent_model_name,
                messages=[
                    {"role": "system", "content": self._build_system_prompt()},
                    {"role": "user", "content": self._build_user_payload(request, existing_memories)},
                ],
                temperature=0,
                extra_body={"enable_thinking": False},
                timeout=self._settings.voice_model_timeout_ms / 1000,
            )
            raw = completion.choices[0].message.content or ""
            payload = json.loads(raw)
            return MemoryOperationPlan(
                operation=payload.get("operation") or request.operation,
                memory_type=payload.get("memory_type") or request.preferred_memory_type or "cold",
                title=str(payload.get("title") or request.title or "").strip(),
                content=str(payload.get("content") or request.content or "").strip(),
                memory_id=str(payload.get("memory_id") or request.memory_id or "").strip(),
                category=str(payload.get("category") or request.category or "general").strip(),
                reason=str(payload.get("reason") or "llm").strip(),
            )
        except Exception:
            return self._fallback.plan(request=request, existing_memories=existing_memories)

    @staticmethod
    def _build_system_prompt() -> str:
        return (
            "你是记忆管理子Agent。你只输出JSON，不输出解释。\n"
            "热记忆用于姓名、年龄、性别等短小且稳定的信息；冷记忆用于住址、电话、爱好、习惯、任务设置等可能变化或较长的信息。\n"
            "字段包括 operation(add/update/delete)、memory_type(hot/cold)、title、content、memory_id、category、reason。\n"
            "删除时如果能通过标题定位，就填写 title；能通过 memory_id 定位就填写 memory_id。"
        )

    @staticmethod
    def _build_user_payload(request: MemoryOperationRequest, existing_memories: list[AgentMemoryRecord]) -> str:
        payload = {
            "request": {
                "operation": request.operation,
                "query": request.query,
                "preferred_memory_type": request.preferred_memory_type,
                "title": request.title,
                "content": request.content,
                "memory_id": request.memory_id,
                "category": request.category,
            },
            "existing_memories": [asdict(item) for item in existing_memories],
        }
        return json.dumps(payload, ensure_ascii=False)


class AgentMemoryRuntime:
    """Agent 长期记忆运行时。

    主要功能：
    1. 每轮向主 Agent 注入热记忆正文和冷记忆标题目录。
    2. 通过 `memory_search` 按冷记忆标题读取详细内容。
    3. 通过记忆管理子 Agent 执行新增、更新和删除。
    """

    def __init__(
        self,
        *,
        store: AgentMemoryStore | None = None,
        enabled: bool = True,
        max_prompt_memories: int = 6,
        manager_agent: MemoryManagementAgent | None = None,
    ) -> None:
        self._store = store or InMemoryAgentMemoryStore()
        self._manager_agent = manager_agent or HeuristicMemoryManagementAgent()
        self.enabled = enabled
        self.max_prompt_memories = max(0, max_prompt_memories)

    def add_memory(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        memory_type: MemoryType,
        title: str,
        content: str,
        category: str = "general",
        source: MemorySource = "agent_inferred",
        confidence: float = 1.0,
        metadata: dict | None = None,
    ) -> AgentMemoryRecord:
        """新增或覆盖一条长期记忆。"""

        if not self.enabled:
            raise ValueError("Agent 记忆功能未启用")
        normalized_scope_id = scope_id.strip()
        normalized_title = self._normalize_title(title)
        normalized_content = self._normalize_content(content)
        if not normalized_scope_id:
            raise ValueError("记忆作用域不能为空")
        if not normalized_title:
            raise ValueError("记忆标题不能为空")
        if not normalized_content:
            raise ValueError("记忆内容不能为空")
        record = AgentMemoryRecord.create(
            scope_type=scope_type,
            scope_id=normalized_scope_id,
            memory_type=memory_type,
            title=normalized_title,
            content=normalized_content,
            category=category.strip() or "general",
            source=source,
            confidence=max(0.0, min(1.0, confidence)),
            metadata=metadata or {},
        )
        upsert_by_title = getattr(self._store, "upsert_by_title", None)
        if callable(upsert_by_title):
            return upsert_by_title(record)
        return self._store.upsert(record)

    def manage_memory(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        request: MemoryOperationRequest,
    ) -> dict[str, Any]:
        """执行记忆管理请求。

        主要逻辑：
        1. 读取当前已有记忆交给记忆管理子 Agent。
        2. 子 Agent 决定冷热分类、标题、内容和具体操作对象。
        3. SDK 负责执行计划并返回完成结果。
        """

        if not self.enabled:
            raise ValueError("Agent 记忆功能未启用")
        existing = self.list_memories(scope_type=scope_type, scope_id=scope_id, limit=100)
        plan = self._manager_agent.plan(request=request, existing_memories=existing)
        if plan.operation in {"add", "update"}:
            record = self.add_memory(
                scope_type=scope_type,
                scope_id=scope_id,
                memory_type=plan.memory_type,
                title=plan.title,
                content=plan.content,
                category=plan.category,
                source=request.source,
                metadata=request.metadata,
            )
            return {"operation": plan.operation, "memory": self.record_to_dict(record), "plan": asdict(plan)}
        deleted = None
        if plan.memory_id:
            deleted = self.delete_memory(memory_id=plan.memory_id, scope_type=scope_type, scope_id=scope_id)
        if deleted is None and plan.title:
            deleted = self.delete_memory_by_title(
                title=plan.title,
                scope_type=scope_type,
                scope_id=scope_id,
                memory_type=plan.memory_type,
            )
        return {
            "operation": "delete",
            "memory": self.record_to_dict(deleted) if deleted is not None else None,
            "plan": asdict(plan),
        }

    def search_memories(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        query: str = "",
        limit: int = 5,
    ) -> list[AgentMemoryRecord]:
        """按关键词查询长期记忆。"""

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

    def search_cold_memories_by_title(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        titles: list[str],
    ) -> list[AgentMemoryRecord]:
        """按标题读取冷记忆详情。"""

        if not self.enabled:
            return []
        return self._store.find_by_titles(
            scope_type=scope_type,
            scope_id=scope_id.strip(),
            titles=titles,
            memory_type="cold",
        )

    def list_memories(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        limit: int = 20,
        memory_type: MemoryType | None = None,
    ) -> list[AgentMemoryRecord]:
        """列出当前作用域下的长期记忆。"""

        if not self.enabled:
            return []
        records = self._store.list_active(scope_type=scope_type, scope_id=scope_id.strip())
        if memory_type is not None:
            records = [record for record in records if record.memory_type == memory_type]
        return records[: max(1, limit)]

    def delete_memory(self, *, memory_id: str, scope_type: MemoryScope, scope_id: str) -> AgentMemoryRecord | None:
        """按编号软删除一条长期记忆。"""

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

    def delete_memory_by_title(
        self,
        *,
        title: str,
        scope_type: MemoryScope,
        scope_id: str,
        memory_type: MemoryType | None = None,
    ) -> AgentMemoryRecord | None:
        """按标题软删除一条长期记忆。"""

        return self._store.delete_by_title(
            title=title,
            scope_type=scope_type,
            scope_id=scope_id.strip(),
            memory_type=memory_type,
        )

    def build_prompt_fragment(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        query: str,
    ) -> str:
        """构造可注入系统提示词的冷热记忆片段。"""

        if not self.enabled or self.max_prompt_memories <= 0:
            return ""
        hot_records = self.list_memories(
            scope_type=scope_type,
            scope_id=scope_id,
            limit=self.max_prompt_memories,
            memory_type="hot",
        )
        cold_records = self.list_memories(
            scope_type=scope_type,
            scope_id=scope_id,
            limit=self.max_prompt_memories,
            memory_type="cold",
        )
        if not hot_records and not cold_records:
            return ""
        lines = [
            "以下是已保存的用户信息。部分信息已直接提供；如果只看到标题且需要详细内容，请调用 memory_search(title 或 titles) 查询后再回答。"
        ]
        if hot_records:
            lines.append("已提供的信息：")
            for record in hot_records:
                lines.append(f"- {record.title}: {record.content}")
        if cold_records:
            lines.append("可查询的信息标题：")
            for record in cold_records:
                lines.append(f"- {record.title}")
        return "\n".join(lines)

    @staticmethod
    def record_to_dict(record: AgentMemoryRecord) -> dict[str, Any]:
        """把记忆对象转换成可返回给 Tool 的字典。"""

        return asdict(record)

    @staticmethod
    def _normalize_title(title: str) -> str:
        """清洗记忆标题。"""

        return " ".join(title.strip().split())[:60]

    @staticmethod
    def _normalize_content(content: str) -> str:
        """清洗记忆正文。"""

        return " ".join(content.strip().split())[:4000]
