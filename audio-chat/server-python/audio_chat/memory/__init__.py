from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from audio_chat.errors import AudioChatError, ErrorCode
from audio_chat.protocol import new_id


@dataclass(frozen=True)
class MemoryRecord:
    """长期记忆记录。

    主要功能：保存某个用户的一条可检索长期记忆。
    主要属性：`memory_id` 是稳定编号，`user_id` 是用户边界，`content`
    是可被检索的文本，`metadata` 保存来源、标签等补充信息。
    """

    memory_id: str
    user_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    deleted: bool = False


class MemoryError(AudioChatError):
    """Memory Service 结构化异常。"""


class MemoryStore:
    """长期记忆存储接口。

    主要功能：定义 Memory Service 需要的写入、搜索和删除能力。
    子类可以使用 jsonl、sqlite 或外部存储实现。
    """

    def write(self, record: MemoryRecord) -> MemoryRecord:
        """写入一条记忆记录。"""

        raise NotImplementedError

    def search(self, *, user_id: str, query: str, limit: int) -> list[MemoryRecord]:
        """搜索用户记忆。"""

        raise NotImplementedError

    def delete(self, *, user_id: str, memory_id: str) -> bool:
        """内部删除记忆。"""

        raise NotImplementedError


class JsonlMemoryStore(MemoryStore):
    """基于 filesystem/jsonl 的第一版 MemoryStore。

    主要功能：每个用户一个 jsonl 文件，便于本地回放、验收和排障。
    主要方法：`write()` 追加记录，`search()` 读取有效记录并做轻量文本匹配，
    `delete()` 追加 tombstone 记录。
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, record: MemoryRecord) -> MemoryRecord:
        """追加写入一条记忆。

        参数：`record` 为完整记忆记录。
        返回值：原记录。
        异常情况：文件不可写时抛出底层 IO 异常。
        """

        with self._path_for(record.user_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        return record

    def search(self, *, user_id: str, query: str, limit: int) -> list[MemoryRecord]:
        """按用户和关键词搜索记忆。

        主要逻辑：读取用户 jsonl，先应用 tombstone，再按 content 和 metadata
        的小写文本做包含匹配。空查询返回最新有效记录。
        参数：`user_id` 为用户编号，`query` 为搜索文本，`limit` 为最大条数。
        返回值：按新到旧排序的 `MemoryRecord`。
        异常情况：记录 JSON 损坏时跳过该行，避免单条坏数据阻塞本地验收。
        """

        records = self._load_effective_records(user_id)
        needle = query.strip().lower()
        if needle:
            records = [
                record
                for record in records
                if needle in record.content.lower()
                or needle in json.dumps(record.metadata, ensure_ascii=False).lower()
            ]
        return sorted(records, key=lambda item: item.created_at, reverse=True)[: max(0, limit)]

    def delete(self, *, user_id: str, memory_id: str) -> bool:
        """追加 tombstone 删除记录。

        参数：`user_id` 为用户编号，`memory_id` 为目标记忆编号。
        返回值：目标记忆存在时返回 True，否则返回 False。
        异常情况：文件不可写时抛出底层 IO 异常。
        """

        exists = any(record.memory_id == memory_id for record in self._load_effective_records(user_id))
        if not exists:
            return False
        tombstone = MemoryRecord(
            memory_id=memory_id,
            user_id=user_id,
            content="",
            metadata={"deleted_by": "MemoryStore.delete"},
            deleted=True,
        )
        self.write(tombstone)
        return True

    def _path_for(self, user_id: str) -> Path:
        safe_user_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in user_id)
        return self.root / f"{safe_user_id}.jsonl"

    def _load_effective_records(self, user_id: str) -> list[MemoryRecord]:
        path = self._path_for(user_id)
        if not path.exists():
            return []
        records: dict[str, MemoryRecord] = {}
        deleted: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                record = MemoryRecord(
                    memory_id=str(raw["memory_id"]),
                    user_id=str(raw["user_id"]),
                    content=str(raw.get("content") or ""),
                    metadata=dict(raw.get("metadata") or {}),
                    created_at=float(raw.get("created_at") or 0),
                    deleted=bool(raw.get("deleted") or False),
                )
            except Exception:
                continue
            if record.deleted:
                deleted.add(record.memory_id)
                records.pop(record.memory_id, None)
                continue
            if record.memory_id not in deleted:
                records[record.memory_id] = record
        return list(records.values())


class MemoryService:
    """长期记忆服务。

    主要功能：为内置 Tool 提供用户级记忆写入、搜索和内部删除。
    主要属性：`enabled` 控制工具是否可用，`store` 是具体存储实现。
    """

    def __init__(self, *, enabled: bool = False, store: MemoryStore | None = None) -> None:
        self.enabled = enabled
        self.store = store or JsonlMemoryStore("runs/audio-chat/memory")

    def write(self, *, user_id: str, content: str, metadata: dict[str, Any] | None = None) -> MemoryRecord:
        """写入用户长期记忆。

        参数：`user_id` 为用户编号，`content` 为记忆正文，`metadata` 为补充信息。
        返回值：写入后的 `MemoryRecord`。
        异常情况：服务未启用或内容为空时抛出 `MemoryError`。
        """

        self._ensure_enabled()
        content = content.strip()
        if not content:
            raise MemoryError("memory content is required", code=ErrorCode.INVALID_ARGUMENT)
        record = MemoryRecord(
            memory_id=new_id("mem"),
            user_id=user_id,
            content=content,
            metadata=dict(metadata or {}),
        )
        return self.store.write(record)

    def search(self, *, user_id: str, query: str, limit: int = 5) -> list[MemoryRecord]:
        """搜索用户长期记忆。"""

        self._ensure_enabled()
        return self.store.search(user_id=user_id, query=query, limit=limit)

    def delete(self, *, user_id: str, memory_id: str) -> bool:
        """内部删除长期记忆。"""

        self._ensure_enabled()
        return self.store.delete(user_id=user_id, memory_id=memory_id)

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise MemoryError("memory service is disabled", code=ErrorCode.PERMISSION_DENIED)


__all__ = [
    "JsonlMemoryStore",
    "MemoryError",
    "MemoryRecord",
    "MemoryService",
    "MemoryStore",
]
