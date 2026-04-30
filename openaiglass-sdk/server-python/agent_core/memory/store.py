"""Agent 长期记忆存储实现。"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from typing import Iterable

from agent_core.context.models import now_ms
from agent_core.memory.models import AgentMemoryRecord, MemoryScope, MemoryType


class AgentMemoryStore:
    """长期记忆存储接口。

    主要功能：
    1. 隔离记忆读写细节。
    2. 允许第一版使用本地文件，后续替换成向量库、图数据库或外部服务。
    """

    def upsert(self, record: AgentMemoryRecord) -> AgentMemoryRecord:
        """新增或覆盖一条记忆。"""

        raise NotImplementedError

    def list_active(self, *, scope_type: MemoryScope, scope_id: str) -> list[AgentMemoryRecord]:
        """列出指定作用域下未删除的记忆。"""

        raise NotImplementedError

    def search(self, *, scope_type: MemoryScope, scope_id: str, query: str, limit: int) -> list[AgentMemoryRecord]:
        """按关键词检索记忆。"""

        raise NotImplementedError

    def find_by_titles(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        titles: list[str],
        memory_type: MemoryType | None = None,
    ) -> list[AgentMemoryRecord]:
        """按标题读取记忆。"""

        raise NotImplementedError

    def delete(self, *, memory_id: str, scope_type: MemoryScope, scope_id: str) -> AgentMemoryRecord | None:
        """软删除指定记忆。"""

        raise NotImplementedError

    def delete_by_title(
        self,
        *,
        title: str,
        scope_type: MemoryScope,
        scope_id: str,
        memory_type: MemoryType | None = None,
    ) -> AgentMemoryRecord | None:
        """按标题软删除指定记忆。"""

        raise NotImplementedError


class InMemoryAgentMemoryStore(AgentMemoryStore):
    """进程内记忆存储。

    主要功能：
    1. 为单元测试和无持久化场景提供轻量实现。
    2. 保持与文件存储相同的软删除语义。
    """

    def __init__(self, records: Iterable[AgentMemoryRecord] | None = None) -> None:
        self._lock = threading.Lock()
        initial_records = list(records or [])
        self._records: dict[str, AgentMemoryRecord] = {record.memory_id: record for record in initial_records}
        self._record_order: dict[str, int] = {
            record.memory_id: index for index, record in enumerate(initial_records)
        }

    def upsert(self, record: AgentMemoryRecord) -> AgentMemoryRecord:
        """新增或覆盖一条记忆。"""

        with self._lock:
            record.updated_at_ms = now_ms()
            if record.memory_id not in self._record_order:
                self._record_order[record.memory_id] = len(self._record_order)
            self._records[record.memory_id] = record
            return record

    def upsert_by_title(self, record: AgentMemoryRecord) -> AgentMemoryRecord:
        """按标题覆盖同作用域同类型记忆。

        主要逻辑：
        1. 同一作用域下标题相同的记忆视为同一槽位。
        2. 写入时复用旧 `memory_id` 和创建时间，避免模型引用失效。
        """

        with self._lock:
            existing = self._find_active_by_title_locked(
                scope_type=record.scope_type,
                scope_id=record.scope_id,
                title=record.title,
                memory_type=record.memory_type,
            )
            if existing is not None:
                record.memory_id = existing.memory_id
                record.created_at_ms = existing.created_at_ms
            if record.memory_id not in self._record_order:
                self._record_order[record.memory_id] = len(self._record_order)
            record.updated_at_ms = now_ms()
            self._records[record.memory_id] = record
            return record

    def list_active(self, *, scope_type: MemoryScope, scope_id: str) -> list[AgentMemoryRecord]:
        """列出指定作用域下未删除的记忆。"""

        with self._lock:
            return sorted(
                [
                    record
                    for record in self._records.values()
                    if record.scope_type == scope_type and record.scope_id == scope_id and record.active
                ],
                key=lambda item: (item.updated_at_ms, self._record_order.get(item.memory_id, -1)),
                reverse=True,
            )

    def search(self, *, scope_type: MemoryScope, scope_id: str, query: str, limit: int) -> list[AgentMemoryRecord]:
        """按简单关键词检索记忆。

        当前实现故意保持轻量：中文场景下先用包含匹配和分词后的字符重叠打分。
        后续如果接入向量库，只需要替换本方法。
        """

        normalized_query = query.strip().lower()
        candidates = self.list_active(scope_type=scope_type, scope_id=scope_id)
        if not normalized_query:
            return candidates[:limit]
        query_terms = {term for term in normalized_query.split() if term}

        scored: list[tuple[int, AgentMemoryRecord]] = []
        for record in candidates:
            text = f"{record.title} {record.content}".lower()
            score = 10 if normalized_query in text else 0
            score += sum(3 for term in query_terms if term in text)
            if not query_terms:
                score += len(set(normalized_query) & set(text))
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: (item[0], item[1].updated_at_ms), reverse=True)
        return [record for _, record in scored[:limit]]

    def find_by_titles(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        titles: list[str],
        memory_type: MemoryType | None = None,
    ) -> list[AgentMemoryRecord]:
        """按标题读取记忆。"""

        normalized_titles = [self._normalize_title(title) for title in titles if self._normalize_title(title)]
        if not normalized_titles:
            return []
        with self._lock:
            result: list[AgentMemoryRecord] = []
            for title in normalized_titles:
                record = self._find_active_by_title_locked(
                    scope_type=scope_type,
                    scope_id=scope_id,
                    title=title,
                    memory_type=memory_type,
                )
                if record is not None:
                    result.append(record)
            return result

    def delete(self, *, memory_id: str, scope_type: MemoryScope, scope_id: str) -> AgentMemoryRecord | None:
        """软删除指定记忆。"""

        with self._lock:
            record = self._records.get(memory_id)
            if record is None or record.scope_type != scope_type or record.scope_id != scope_id:
                return None
            record.deleted_at_ms = now_ms()
            record.updated_at_ms = record.deleted_at_ms
            return record

    def delete_by_title(
        self,
        *,
        title: str,
        scope_type: MemoryScope,
        scope_id: str,
        memory_type: MemoryType | None = None,
    ) -> AgentMemoryRecord | None:
        """按标题软删除指定记忆。"""

        normalized_title = self._normalize_title(title)
        if not normalized_title:
            return None
        with self._lock:
            record = self._find_active_by_title_locked(
                scope_type=scope_type,
                scope_id=scope_id,
                title=normalized_title,
                memory_type=memory_type,
            )
            if record is None:
                return None
            record.deleted_at_ms = now_ms()
            record.updated_at_ms = record.deleted_at_ms
            return record

    def _find_active_by_title_locked(
        self,
        *,
        scope_type: MemoryScope,
        scope_id: str,
        title: str,
        memory_type: MemoryType | None = None,
    ) -> AgentMemoryRecord | None:
        normalized_title = self._normalize_title(title)
        for record in self._records.values():
            if record.scope_type != scope_type or record.scope_id != scope_id or not record.active:
                continue
            if memory_type is not None and record.memory_type != memory_type:
                continue
            if self._normalize_title(record.title) == normalized_title:
                return record
        return None

    @staticmethod
    def _normalize_title(title: str) -> str:
        return " ".join(title.strip().lower().split())


class JsonFileAgentMemoryStore(InMemoryAgentMemoryStore):
    """JSON 文件记忆存储。

    主要功能：
    1. 使用单个 JSON 文件保存长期记忆，便于本地开发和回放排障。
    2. 写入时采用临时文件替换，降低异常退出造成半文件的概率。

    主要属性：
    1. `path`：记忆文件路径。
    """

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(records=self._load_records())

    def upsert(self, record: AgentMemoryRecord) -> AgentMemoryRecord:
        """新增或覆盖一条记忆并同步落盘。"""

        result = super().upsert(record)
        self._flush()
        return result

    def upsert_by_title(self, record: AgentMemoryRecord) -> AgentMemoryRecord:
        """按标题覆盖记忆并同步落盘。"""

        result = super().upsert_by_title(record)
        self._flush()
        return result

    def delete(self, *, memory_id: str, scope_type: MemoryScope, scope_id: str) -> AgentMemoryRecord | None:
        """软删除指定记忆并同步落盘。"""

        result = super().delete(memory_id=memory_id, scope_type=scope_type, scope_id=scope_id)
        if result is not None:
            self._flush()
        return result

    def delete_by_title(
        self,
        *,
        title: str,
        scope_type: MemoryScope,
        scope_id: str,
        memory_type: MemoryType | None = None,
    ) -> AgentMemoryRecord | None:
        """按标题软删除指定记忆并同步落盘。"""

        result = super().delete_by_title(
            title=title,
            scope_type=scope_type,
            scope_id=scope_id,
            memory_type=memory_type,
        )
        if result is not None:
            self._flush()
        return result

    def _load_records(self) -> list[AgentMemoryRecord]:
        if not self.path or not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as file:
            payload = json.load(file)
        if not isinstance(payload, list):
            return []
        records: list[AgentMemoryRecord] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                if "content" not in item and "text" in item:
                    item["content"] = item.get("text") or ""
                if "memory_type" not in item:
                    item["memory_type"] = "personalized"
                if item.get("memory_type") not in {"basic", "personalized"}:
                    continue
                if "title" not in item:
                    item["title"] = item.get("category") or "未命名记忆"
                item.pop("text", None)
                item.pop("category", None)
                records.append(AgentMemoryRecord(**item))
            except TypeError:
                continue
        return records

    def _flush(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp_path = f"{self.path}.tmp"
        with self._lock:
            payload = [asdict(record) for record in self._records.values()]
            with open(tmp_path, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(tmp_path, self.path)
