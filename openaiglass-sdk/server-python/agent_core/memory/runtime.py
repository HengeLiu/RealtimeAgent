"""Agent 长期记忆运行时。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from agent_core.memory.models import AgentMemoryRecord, MemoryScope, MemorySource, MemoryType
from agent_core.memory.store import AgentMemoryStore, JsonFileAgentMemoryStore

MemoryOperation = Literal["add", "update", "delete"]


@dataclass(slots=True)
class MemoryOperationRequest:
    """记忆管理请求。

    主要功能：
    1. 承载主 Agent 对记忆维护的自然语言请求。
    2. 保留主 Agent 从历史聊天中摘取的相关上下文，让 MemoryAgent 自行决定具体动作。
    """

    query: str
    memory_context: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryOperationAction:
    """MemoryAgent 输出的一条内部记忆动作。"""

    operation: MemoryOperation
    title: str
    content: str = ""
    memory_type: MemoryType = "personalized"
    memory_id: str = ""


@dataclass(slots=True)
class MemoryOperationPlan:
    """MemoryAgent 输出的结构化维护计划。

    主要功能：
    1. 支持一次自然语言请求拆成多条内部动作串行执行。
    2. `feedback` 是返回给主 Agent 的简短文本，不暴露内部 `memory_id`。
    """

    actions: list[MemoryOperationAction]
    feedback: str = "记忆已处理"


class MemoryManagementAgent:
    """记忆管理子 Agent 接口。

    主要功能：
    1. 根据已有记忆、用户原始请求和相关聊天上下文生成维护计划。
    2. 输出内部动作列表，真正落盘仍由 SDK 运行时执行。
    """

    def plan(
        self,
        *,
        request: MemoryOperationRequest,
        existing_memories: list[AgentMemoryRecord],
    ) -> MemoryOperationPlan:
        """生成记忆操作计划。"""

        raise NotImplementedError


class LlmMemoryManagementAgent(MemoryManagementAgent):
    """基于大模型的记忆管理子 Agent。

    主要功能：
    1. 使用服务端模型把自然语言请求转换成一组结构化记忆动作。
    2. 不提供启发式降级；模型不可用时，记忆维护也应明确失败。
    """

    def __init__(self, *, settings) -> None:
        self._settings = settings

    def plan(
        self,
        *,
        request: MemoryOperationRequest,
        existing_memories: list[AgentMemoryRecord],
    ) -> MemoryOperationPlan:
        """调用大模型生成记忆操作计划。"""

        if not getattr(self._settings, "dashscope_api_key", "").strip():
            raise ValueError("记忆管理需要可用的大模型配置")
        from openai import OpenAI

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
        actions: list[MemoryOperationAction] = []
        for item in payload.get("actions") or []:
            if not isinstance(item, dict):
                continue
            operation = str(item.get("operation") or "").strip()
            if operation not in {"add", "update", "delete"}:
                continue
            memory_type = str(item.get("memory_type") or "personalized").strip()
            if memory_type not in {"basic", "personalized"}:
                memory_type = "personalized"
            actions.append(
                MemoryOperationAction(
                    operation=operation,  # type: ignore[arg-type]
                    title=str(item.get("title") or "").strip(),
                    content=str(item.get("content") or "").strip(),
                    memory_type=memory_type,  # type: ignore[arg-type]
                    memory_id=str(item.get("memory_id") or "").strip(),
                )
            )
        feedback = str(payload.get("feedback") or "").strip() or "记忆已处理"
        return MemoryOperationPlan(actions=actions, feedback=feedback)

    @staticmethod
    def _build_system_prompt() -> str:
        return (
            "你是记忆管理子Agent。你只输出JSON，不输出解释。\n"
            "你要根据用户原始请求、相关聊天上下文和已有记忆，决定是否需要新增、更新或删除长期记忆。\n"
            "基本信息用于姓名、年龄、性别、称呼等短小稳定信息；个性化信息用于住址、电话、爱好、习惯、任务设置等可能变化或较长的信息。\n"
            "可以输出多条actions并按顺序执行，例如先delete再add。\n"
            "每条action字段只能包括 operation(add/update/delete)、memory_type(basic/personalized)、title、content、memory_id。\n"
            "memory_id只用于定位已有记忆，不能出现在feedback中。\n"
            "如果只需要更新已有记忆，优先填写已有记忆中的memory_id；新增时不要填写memory_id。\n"
            "删除时如果能通过memory_id定位就填写memory_id，否则填写title。\n"
            "不要保存API Key、设备token、WiFi密码、真实用户媒体数据、一次性任务状态或未经确认的推断。\n"
            "输出格式：{\"actions\":[...],\"feedback\":\"给主Agent的简短中文反馈\"}"
        )

    @staticmethod
    def _build_user_payload(request: MemoryOperationRequest, existing_memories: list[AgentMemoryRecord]) -> str:
        payload = {
            "request": {
                "query": request.query,
                "memory_context": request.memory_context,
            },
            "existing_memories": [AgentMemoryRuntime.record_to_internal_dict(item) for item in existing_memories],
        }
        return json.dumps(payload, ensure_ascii=False)


class AgentMemoryRuntime:
    """Agent 长期记忆运行时。

    主要功能：
    1. 每轮向主 Agent 注入基本信息正文和个性化信息标题目录。
    2. 通过 `memory_search` 按记忆标题读取详细内容。
    3. 通过记忆管理子 Agent 执行一组串行新增、更新和删除动作。
    """

    def __init__(
        self,
        *,
        store: AgentMemoryStore | None = None,
        enabled: bool = True,
        max_prompt_memories: int = 6,
        manager_agent: MemoryManagementAgent | None = None,
    ) -> None:
        self._store = store or JsonFileAgentMemoryStore("runs/memory/agent_memories.json")
        self._manager_agent = manager_agent
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
        if memory_type not in {"basic", "personalized"}:
            raise ValueError("记忆类型必须是 basic 或 personalized")
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
        1. 读取当前已有记忆交给 MemoryAgent。
        2. MemoryAgent 自行决定一组内部动作。
        3. SDK 按顺序执行动作并返回给主 Agent 的文本反馈。
        """

        if not self.enabled:
            raise ValueError("Agent 记忆功能未启用")
        if self._manager_agent is None:
            raise ValueError("记忆管理需要可用的大模型管理 Agent")
        existing = self.list_memories(scope_type=scope_type, scope_id=scope_id, limit=100)
        plan = self._manager_agent.plan(request=request, existing_memories=existing)
        results: list[dict[str, Any]] = []
        for action in plan.actions:
            record = self._execute_action(
                scope_type=scope_type,
                scope_id=scope_id,
                action=action,
                request=request,
            )
            results.append(
                {
                    "operation": action.operation,
                    "title": action.title or (record.title if record is not None else ""),
                    "success": record is not None,
                }
            )
        feedback = plan.feedback.strip() or self._build_feedback(results)
        return {
            "feedback": feedback,
            "actions": results,
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

    def search_memories_by_title(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        titles: list[str],
    ) -> list[AgentMemoryRecord]:
        """按标题读取记忆详情。"""

        if not self.enabled:
            return []
        return self._store.find_by_titles(
            scope_type=scope_type,
            scope_id=scope_id.strip(),
            titles=titles,
            memory_type=None,
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

    def _execute_action(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        action: MemoryOperationAction,
        request: MemoryOperationRequest,
    ) -> AgentMemoryRecord | None:
        """执行单条内部记忆动作。"""

        if action.operation == "add":
            return self.add_memory(
                scope_type=scope_type,
                scope_id=scope_id,
                memory_type=action.memory_type,
                title=action.title,
                content=action.content,
                source="agent_inferred",
                metadata=request.metadata,
            )
        if action.operation == "update":
            return self._apply_update_action(
                scope_type=scope_type,
                scope_id=scope_id,
                action=action,
                request=request,
            )
        if action.memory_id:
            deleted = self.delete_memory(memory_id=action.memory_id, scope_type=scope_type, scope_id=scope_id)
            if deleted is not None:
                return deleted
        if action.title:
            return self.delete_memory_by_title(
                title=action.title,
                scope_type=scope_type,
                scope_id=scope_id,
                memory_type=None,
            )
        return None

    def _apply_update_action(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        action: MemoryOperationAction,
        request: MemoryOperationRequest,
    ) -> AgentMemoryRecord:
        """执行更新动作，尽量复用已有记忆编号。"""

        target = self._find_update_target(action=action, scope_type=scope_type, scope_id=scope_id)
        if target is None:
            return self.add_memory(
                scope_type=scope_type,
                scope_id=scope_id,
                memory_type=action.memory_type,
                title=action.title,
                content=action.content,
                source="agent_inferred",
                metadata=request.metadata,
            )
        title = self._normalize_title(action.title or target.title)
        content = self._normalize_content(action.content or target.content)
        if not title:
            raise ValueError("记忆标题不能为空")
        if not content:
            raise ValueError("记忆内容不能为空")
        updated = AgentMemoryRecord(
            memory_id=target.memory_id,
            scope_type=target.scope_type,
            scope_id=target.scope_id,
            memory_type=action.memory_type or target.memory_type,
            title=title,
            content=content,
            source=target.source,
            confidence=target.confidence,
            metadata={**target.metadata, **request.metadata},
            created_at_ms=target.created_at_ms,
            deleted_at_ms=None,
        )
        return self._store.upsert(updated)

    def _find_update_target(
        self,
        *,
        action: MemoryOperationAction,
        scope_type: MemoryScope,
        scope_id: str,
    ) -> AgentMemoryRecord | None:
        """按编号和标题寻找要更新的记忆。"""

        existing = self.list_memories(scope_type=scope_type, scope_id=scope_id, limit=100)
        if action.memory_id:
            matched = next((item for item in existing if item.memory_id == action.memory_id), None)
            if matched is not None:
                return matched
        normalized_title = self._normalize_title(action.title)
        if normalized_title:
            return next((item for item in existing if self._normalize_title(item.title) == normalized_title), None)
        return None

    def build_prompt_fragment(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        query: str,
    ) -> str:
        """构造可注入系统提示词的长期记忆片段。"""

        if not self.enabled or self.max_prompt_memories <= 0:
            return ""
        basic_records = self.list_memories(
            scope_type=scope_type,
            scope_id=scope_id,
            limit=self.max_prompt_memories,
            memory_type="basic",
        )
        personalized_records = self.list_memories(
            scope_type=scope_type,
            scope_id=scope_id,
            limit=self.max_prompt_memories,
            memory_type="personalized",
        )
        if not basic_records and not personalized_records:
            return ""
        lines = [
            "以下是已保存的用户信息。基本信息已直接提供；个性化信息只提供标题，如果需要详细内容，请调用 memory_search(title 或 titles) 查询后再回答。"
        ]
        if basic_records:
            lines.append("基本信息：")
            for record in basic_records:
                lines.append(f"- {record.title}: {record.content}")
        if personalized_records:
            lines.append("个性化信息标题：")
            for record in personalized_records:
                lines.append(f"- {record.title}")
        return "\n".join(lines)

    @staticmethod
    def record_to_internal_dict(record: AgentMemoryRecord) -> dict[str, Any]:
        """把记忆对象转换成 MemoryAgent 可见的内部字典。"""

        return asdict(record)

    @staticmethod
    def record_to_public_dict(record: AgentMemoryRecord) -> dict[str, Any]:
        """把记忆对象转换成主 Agent 可见的公开字典。"""

        return {
            "memory_type": record.memory_type,
            "title": record.title,
            "content": record.content,
        }

    @staticmethod
    def record_to_dict(record: AgentMemoryRecord) -> dict[str, Any]:
        """兼容旧调用面，返回不含内部编号的公开字典。"""

        return AgentMemoryRuntime.record_to_public_dict(record)

    @staticmethod
    def _build_feedback(results: list[dict[str, Any]]) -> str:
        """根据动作结果生成简短反馈。"""

        if not results:
            return "没有需要更新的记忆"
        succeeded = [item for item in results if item.get("success")]
        if not succeeded:
            return "没有找到需要处理的记忆"
        return "记忆已更新"

    @staticmethod
    def _normalize_title(title: str) -> str:
        """清洗记忆标题。"""

        return " ".join(title.strip().split())[:60]

    @staticmethod
    def _normalize_content(content: str) -> str:
        """清洗记忆正文。"""

        return " ".join(content.strip().split())[:4000]
