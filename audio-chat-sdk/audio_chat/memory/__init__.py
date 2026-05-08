from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from audio_chat.errors import AudioChatError, ErrorCode
from audio_chat.protocol import new_id


MemoryType = Literal["basic", "personalized"]
MemorySource = Literal["user_requested", "agent_inferred", "system"]


@dataclass(frozen=True)
class MemoryRecord:
    """长期记忆记录。

    主要功能：保存某个用户的一条长期记忆，并区分基本信息和个性化信息。
    主要属性：`memory_type` 为 `basic` 或 `personalized`；`topic` 是记忆主题，
    同一用户下 `memory_type + topic` 表示一个可更新槽位。
    """

    memory_id: str
    user_id: str
    content: str
    memory_type: MemoryType = "personalized"
    topic: str = ""
    source: MemorySource = "agent_inferred"
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
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

    def find_by_topics(self, *, user_id: str, topics: list[str], limit: int = 20) -> list[MemoryRecord]:
        """按主题读取用户记忆。"""

        raise NotImplementedError

    def list_records(self, *, user_id: str, limit: int = 20, memory_type: MemoryType | None = None) -> list[MemoryRecord]:
        """列出用户记忆。"""

        raise NotImplementedError

    def delete(self, *, user_id: str, memory_id: str) -> bool:
        """内部删除记忆。"""

        raise NotImplementedError


class JsonlMemoryStore(MemoryStore):
    """基于 filesystem/json 的第一版 MemoryStore。

    主要功能：每个用户一个 `memory.json` 文件，便于本地回放、验收和排障。
    主要方法：`write()` 按 `memory_type + topic` 覆盖同槽位记录，`search()` 做轻量文本匹配，
    `find_by_topics()` 按主题取详情，`delete()` 删除指定记录。
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

        records = self._load_effective_records(record.user_id)
        now = time.time()
        replacement = record
        for index, existing in enumerate(records):
            if existing.memory_type == record.memory_type and self._normalize_topic(existing.topic) == self._normalize_topic(record.topic):
                replacement = MemoryRecord(
                    memory_id=existing.memory_id,
                    user_id=record.user_id,
                    content=record.content,
                    memory_type=record.memory_type,
                    topic=record.topic,
                    source=record.source,
                    confidence=record.confidence,
                    metadata={**existing.metadata, **record.metadata},
                    created_at=existing.created_at,
                    updated_at=now,
                    deleted=False,
                )
                records[index] = replacement
                self._flush(record.user_id, records)
                return replacement
        replacement = MemoryRecord(
            memory_id=record.memory_id,
            user_id=record.user_id,
            content=record.content,
            memory_type=record.memory_type,
            topic=record.topic,
            source=record.source,
            confidence=record.confidence,
            metadata=record.metadata,
            created_at=record.created_at,
            updated_at=now,
            deleted=False,
        )
        records.append(replacement)
        self._flush(record.user_id, records)
        return replacement

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
                if needle in record.topic.lower()
                or needle in record.content.lower()
                or needle in json.dumps(record.metadata, ensure_ascii=False).lower()
            ]
        return sorted(records, key=lambda item: item.updated_at, reverse=True)[: max(0, limit)]

    def find_by_topics(self, *, user_id: str, topics: list[str], limit: int = 20) -> list[MemoryRecord]:
        """按主题读取用户记忆详情。

        参数：`user_id` 为用户编号，`topics` 为主 Agent 可见的主题列表。
        返回值：按 topics 顺序返回匹配记录。
        异常情况：无匹配时返回空列表。
        """

        normalized_topics = [self._normalize_topic(topic) for topic in topics if self._normalize_topic(topic)]
        if not normalized_topics:
            return []
        records = self._load_effective_records(user_id)
        result: list[MemoryRecord] = []
        for topic in normalized_topics:
            matched = next((record for record in records if self._normalize_topic(record.topic) == topic), None)
            if matched is not None:
                result.append(matched)
            if len(result) >= limit:
                break
        return result

    def list_records(self, *, user_id: str, limit: int = 20, memory_type: MemoryType | None = None) -> list[MemoryRecord]:
        """列出用户记忆。"""

        records = self._load_effective_records(user_id)
        if memory_type is not None:
            records = [record for record in records if record.memory_type == memory_type]
        return sorted(records, key=lambda item: item.updated_at, reverse=True)[: max(1, limit)]

    def delete(self, *, user_id: str, memory_id: str) -> bool:
        """追加 tombstone 删除记录。

        参数：`user_id` 为用户编号，`memory_id` 为目标记忆编号。
        返回值：目标记忆存在时返回 True，否则返回 False。
        异常情况：文件不可写时抛出底层 IO 异常。
        """

        records = self._load_effective_records(user_id)
        remaining = [record for record in records if record.memory_id != memory_id]
        if len(remaining) == len(records):
            return False
        self._flush(user_id, remaining)
        return True

    def _path_for(self, user_id: str) -> Path:
        safe_user_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in user_id)
        return self.root / safe_user_id / "memory.json"

    def _legacy_path_for(self, user_id: str) -> Path:
        safe_user_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in user_id)
        return self.root / f"{safe_user_id}.jsonl"

    def _load_raw_records(self, user_id: str) -> list[dict[str, Any]]:
        """读取用户 memory 原始记录。

        主要逻辑：优先读取新版 `memory.json` 数组；如果不存在，则兼容读取旧版
        `<user_id>.jsonl`，方便已有开发数据迁移。
        参数：`user_id` 为用户编号。
        返回值：原始 dict 列表。
        异常情况：JSON 损坏时返回空列表，避免阻塞本地开发。
        """

        path = self._path_for(user_id)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8") or "[]")
            except Exception:
                return []
            if isinstance(data, list):
                return [dict(item) for item in data if isinstance(item, dict)]
            return []
        legacy_path = self._legacy_path_for(user_id)
        if not legacy_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in legacy_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except Exception:
                continue
            if isinstance(raw, dict):
                rows.append(raw)
        return rows

    def _load_effective_records(self, user_id: str) -> list[MemoryRecord]:
        records: dict[str, MemoryRecord] = {}
        deleted: set[str] = set()
        for raw in self._load_raw_records(user_id):
            try:
                record = MemoryRecord(
                    memory_id=str(raw["memory_id"]),
                    user_id=str(raw["user_id"]),
                    content=str(raw.get("content") or ""),
                    memory_type=self._normalize_memory_type(raw.get("memory_type")),
                    topic=self._normalize_topic(str(raw.get("topic") or raw.get("metadata", {}).get("topic") or "默认记忆")),
                    source=self._normalize_source(raw.get("source")),
                    confidence=float(raw.get("confidence") or 1.0),
                    metadata=dict(raw.get("metadata") or {}),
                    created_at=float(raw.get("created_at") or 0),
                    updated_at=float(raw.get("updated_at") or raw.get("created_at") or 0),
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

    def _flush(self, user_id: str, records: list[MemoryRecord]) -> None:
        path = self._path_for(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        payload = [asdict(record) for record in records if not record.deleted]
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(path)

    @staticmethod
    def _normalize_topic(topic: str) -> str:
        return " ".join(str(topic or "").strip().split())[:60]

    @staticmethod
    def _normalize_memory_type(value: Any) -> MemoryType:
        return "basic" if str(value or "").strip() == "basic" else "personalized"

    @staticmethod
    def _normalize_source(value: Any) -> MemorySource:
        source = str(value or "").strip()
        if source in {"user_requested", "agent_inferred", "system"}:
            return source  # type: ignore[return-value]
        return "agent_inferred"


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
        metadata = dict(metadata or {})
        return self.add_memory(
            user_id=user_id,
            memory_type=str(metadata.get("memory_type") or "personalized"),
            topic=str(metadata.get("topic") or _infer_memory_topic(content)),
            content=content,
            source=str(metadata.get("source") or "agent_inferred"),
            metadata=metadata,
        )

    def add_memory(
        self,
        *,
        user_id: str,
        memory_type: str,
        topic: str,
        content: str,
        source: str = "agent_inferred",
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        """按类型和主题新增或覆盖一条长期记忆。"""

        self._ensure_enabled()
        normalized_type: MemoryType = "basic" if memory_type == "basic" else "personalized"
        normalized_topic = " ".join(str(topic or "").strip().split())[:60]
        normalized_content = " ".join(str(content or "").strip().split())[:4000]
        if not normalized_topic:
            raise MemoryError("memory topic is required", code=ErrorCode.INVALID_ARGUMENT)
        if not normalized_content:
            raise MemoryError("memory content is required", code=ErrorCode.INVALID_ARGUMENT)
        normalized_source: MemorySource = source if source in {"user_requested", "agent_inferred", "system"} else "agent_inferred"  # type: ignore[assignment]
        return self.store.write(
            MemoryRecord(
                memory_id=new_id("mem"),
                user_id=user_id,
                memory_type=normalized_type,
                topic=normalized_topic,
                content=normalized_content,
                source=normalized_source,
                metadata=dict(metadata or {}),
            )
        )

    def manage(self, *, user_id: str, memory_context: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """根据一段自然语言上下文维护长期记忆。

        主要逻辑：第一版在 SDK 内做保守规则解析，保持与老 SDK 相同的工具入参和两层存储格式；
        后续可以替换成独立 MemoryAgent，但不改变 Tool 协议。
        """

        self._ensure_enabled()
        context = " ".join(str(memory_context or "").strip().split())
        if not context:
            raise MemoryError("memory_context is required", code=ErrorCode.INVALID_ARGUMENT)
        lowered = context.lower()
        if any(word in context for word in ("忘记", "删除")) or "forget" in lowered:
            query = context
            for marker in ("忘记", "删除", "请", "帮我"):
                query = query.replace(marker, "")
            matches = self.search(user_id=user_id, query=query.strip() or context, limit=1)
            if not matches:
                return {"feedback": "没有找到需要处理的记忆", "actions": []}
            deleted = self.delete(user_id=user_id, memory_id=matches[0].memory_id)
            return {
                "feedback": "记忆已更新" if deleted else "没有找到需要处理的记忆",
                "actions": [{"operation": "delete", "topic": matches[0].topic, "success": deleted}],
            }
        memory_type = _infer_memory_type(context)
        topic = _infer_memory_topic(context, memory_type=memory_type)
        content = _normalize_memory_content(context, topic=topic, memory_type=memory_type)
        record = self.add_memory(
            user_id=user_id,
            memory_type=memory_type,
            topic=topic,
            content=content,
            source="user_requested" if "记住" in context else "agent_inferred",
            metadata=dict(metadata or {}),
        )
        return {
            "feedback": "记忆已更新",
            "actions": [{"operation": "update", "memory_type": record.memory_type, "topic": record.topic, "success": True}],
        }

    def search(self, *, user_id: str, query: str, limit: int = 5) -> list[MemoryRecord]:
        """搜索用户长期记忆。"""

        self._ensure_enabled()
        return self.store.search(user_id=user_id, query=query, limit=limit)

    def search_by_topics(self, *, user_id: str, topics: list[str], limit: int = 20) -> list[MemoryRecord]:
        """按主题读取用户长期记忆详情。"""

        self._ensure_enabled()
        return self.store.find_by_topics(user_id=user_id, topics=topics, limit=limit)

    def list_records(self, *, user_id: str, limit: int = 20, memory_type: MemoryType | None = None) -> list[MemoryRecord]:
        """列出用户长期记忆。"""

        self._ensure_enabled()
        return self.store.list_records(user_id=user_id, limit=limit, memory_type=memory_type)

    def build_prompt_fragment(self, *, user_id: str, max_items: int = 6) -> str:
        """构造注入主 Agent system prompt 的长期记忆片段。"""

        if not self.enabled or max_items <= 0:
            return ""
        basic_records = self.store.list_records(user_id=user_id, limit=max_items, memory_type="basic")
        personalized_records = self.store.list_records(user_id=user_id, limit=max_items, memory_type="personalized")
        if not basic_records and not personalized_records:
            return ""
        lines = [
            "以下是已保存的用户信息。基本信息已直接提供；个性化信息只提供主题，如果需要详细内容，请调用 memory_search(topic 或 topics) 查询后再回答。"
        ]
        if basic_records:
            lines.append("基本信息：")
            for record in basic_records:
                lines.append(f"- {record.topic}: {record.content}")
        if personalized_records:
            lines.append("个性化信息主题：")
            for record in personalized_records:
                lines.append(f"- {record.topic}")
        return "\n".join(lines)

    def delete(self, *, user_id: str, memory_id: str) -> bool:
        """内部删除长期记忆。"""

        self._ensure_enabled()
        return self.store.delete(user_id=user_id, memory_id=memory_id)

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise MemoryError("memory service is disabled", code=ErrorCode.PERMISSION_DENIED)


def memory_record_to_public_dict(record: MemoryRecord) -> dict[str, Any]:
    """转换成主 Agent 可见的记忆详情。"""

    return {"memory_type": record.memory_type, "topic": record.topic, "content": record.content}


def _infer_memory_type(content: str) -> MemoryType:
    basic_keywords = ("我叫", "名字", "姓名", "年龄", "岁", "性别", "称呼", "叫我", "语言偏好", "沟通偏好")
    return "basic" if any(keyword in content for keyword in basic_keywords) else "personalized"


def _infer_memory_topic(content: str, *, memory_type: MemoryType = "personalized") -> str:
    if "我叫" in content or "名字" in content or "姓名" in content:
        return "姓名"
    if "年龄" in content or "岁" in content:
        return "年龄"
    if "称呼" in content or "叫我" in content:
        return "称呼"
    if "语言" in content:
        return "语言偏好"
    if "沟通" in content or "简短" in content:
        return "沟通偏好"
    if "住" in content or "地址" in content or "家" in content:
        return "住址"
    if "水杯" in content:
        return "水杯位置"
    if "手机" in content:
        return "手机位置"
    if "电梯" in content:
        return "电梯位置"
    if "楼梯" in content:
        return "楼梯偏好"
    if "导航" in content or "路线" in content:
        return "导航偏好"
    if "习惯" in content or "常去" in content:
        return "出行习惯"
    return "基本信息" if memory_type == "basic" else "用户偏好"


def _normalize_memory_content(content: str, *, topic: str, memory_type: MemoryType) -> str:
    normalized = " ".join(content.strip().split())
    if topic == "姓名" and "我叫" in normalized:
        name = normalized.split("我叫", 1)[1].split("，", 1)[0].split(",", 1)[0].strip("。 .")
        if name:
            return f"用户名字叫{name}。"
    return normalized


__all__ = [
    "JsonlMemoryStore",
    "MemoryError",
    "MemoryRecord",
    "MemoryService",
    "MemoryStore",
    "MemoryType",
    "memory_record_to_public_dict",
]
