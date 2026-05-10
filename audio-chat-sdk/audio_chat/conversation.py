from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class MessageSummary:
    """对一段历史对话的压缩摘要。

    主要功能：记录被压缩消息的时间范围、归档文件和摘要正文。
    主要属性：`history_file` 指向原始消息备份，`content` 会进入后续模型上下文。
    """

    summary_id: str
    user_id: str
    device_id: str
    source_message_count: int
    source_start_at: float
    source_end_at: float
    history_file: str
    content: str
    created_at: float


class MessageSummarizer(Protocol):
    """消息摘要器接口。

    主要功能：把一批即将归档的 active messages 压缩成一段可放入模型上下文的摘要。
    """

    def summarize(self, messages: list[dict[str, Any]]) -> str:
        """生成摘要文本。"""


class RuleBasedMessageSummarizer:
    """轻量规则摘要器。

    主要功能：在未配置专用 LLM 摘要器前，用确定性规则保留历史对话的关键文本。
    主要方法：`summarize()` 按角色提取 user/assistant 文本并控制长度。
    """

    def __init__(self, max_chars: int = 1600) -> None:
        self.max_chars = max(200, int(max_chars or 1600))

    def summarize(self, messages: list[dict[str, Any]]) -> str:
        """生成一段稳定、可读的历史摘要。

        参数：`messages` 为被压缩的 active messages。
        返回值：中文摘要文本。
        异常情况：无；空消息返回固定提示。
        """

        lines: list[str] = []
        for message in messages:
            role = str(message.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            content = _compact_text(message.get("content"), limit=220)
            if not content:
                continue
            label = "用户" if role == "user" else "助手"
            lines.append(f"{label}: {content}")
        if not lines:
            return "本段历史对话没有可提取的用户或助手文本。"
        summary = "本段历史对话要点：\n" + "\n".join(f"- {line}" for line in lines)
        return summary if len(summary) <= self.max_chars else f"{summary[: self.max_chars].rstrip()}..."


class ConversationMemoryService:
    """会话消息维护服务。

    主要功能：统一维护 active_messages、history_messages 和 message_summaries。
    主要属性：`root` 是 runs 根目录；每个用户设备目录下保存 active、summary 和 history。
    """

    ACTIVE_FILE = "active-messages.jsonl"
    LEGACY_MESSAGES_FILE = "messages.jsonl"
    SUMMARY_FILE = "message-summaries.jsonl"

    def __init__(self, root: str | Path, *, summarizer: MessageSummarizer | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.summarizer = summarizer or RuleBasedMessageSummarizer()

    def append_message(self, *, user_id: str, device_id: str, message: dict[str, Any]) -> None:
        """追加一条 active message。

        主要逻辑：写入 canonical `active-messages.jsonl`，同时同步旧入口
        `messages.jsonl`，便于现有排障脚本继续读取。
        参数：`user_id/device_id` 定位设备对话，`message` 为待保存消息。
        返回值：无。
        异常情况：文件不可写时抛出底层 IO 异常。
        """

        normalized = _normalize_record(message, user_id=user_id, device_id=device_id)
        active_path = self._device_dir(user_id, device_id) / self.ACTIVE_FILE
        legacy_path = self._device_dir(user_id, device_id) / self.LEGACY_MESSAGES_FILE
        self._initialize_active_from_legacy(active_path=active_path, legacy_path=legacy_path)
        self._append_jsonl(active_path, normalized)
        self._append_jsonl(legacy_path, normalized)

    def legacy_messages_path(self, *, user_id: str, device_id: str) -> Path:
        """返回兼容 `messages.jsonl` 文件路径。

        主要逻辑：供日志层展示真实落盘位置，避免为了拼日志路径误创建 `_unbound`
        目录。
        参数：`user_id/device_id` 定位用户设备。
        返回值：兼容消息文件路径。
        异常情况：无。
        """

        return self._device_dir(user_id, device_id) / self.LEGACY_MESSAGES_FILE

    def load_active_messages(self, *, user_id: str, device_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """读取当前可直接进入模型上下文的 active messages。

        主要逻辑：优先读取 `active-messages.jsonl`；首次升级时如果只存在旧
        `messages.jsonl`，会把旧内容迁移成 active 文件。
        参数：`limit` 为最多返回条数。
        返回值：按原始顺序返回最近消息。
        异常情况：文件不存在时返回空列表。
        """

        if not user_id or not device_id or limit <= 0:
            return []
        active_path = self._device_dir(user_id, device_id) / self.ACTIVE_FILE
        legacy_path = self._device_dir(user_id, device_id) / self.LEGACY_MESSAGES_FILE
        self._initialize_active_from_legacy(active_path=active_path, legacy_path=legacy_path)
        records = self._read_jsonl(active_path)
        return records[-limit:]

    def load_latest_summary(self, *, user_id: str, device_id: str) -> MessageSummary | None:
        """读取最近一次消息压缩摘要。"""

        summaries = self.load_summaries(user_id=user_id, device_id=device_id, limit=1)
        return summaries[-1] if summaries else None

    def load_summaries(self, *, user_id: str, device_id: str, limit: int = 20) -> list[MessageSummary]:
        """读取消息摘要记录。"""

        path = self._device_dir(user_id, device_id) / self.SUMMARY_FILE
        records = self._read_jsonl(path)
        summaries: list[MessageSummary] = []
        for item in records[-max(1, limit) :]:
            try:
                summaries.append(
                    MessageSummary(
                        summary_id=str(item["summary_id"]),
                        user_id=str(item["user_id"]),
                        device_id=str(item["device_id"]),
                        source_message_count=int(item["source_message_count"]),
                        source_start_at=float(item["source_start_at"]),
                        source_end_at=float(item["source_end_at"]),
                        history_file=str(item["history_file"]),
                        content=str(item["content"]),
                        created_at=float(item["created_at"]),
                    )
                )
            except Exception:
                continue
        return summaries

    def build_summary_prompt_fragment(self, *, user_id: str, device_id: str) -> str:
        """构造最新历史摘要提示词片段。"""

        summary = self.load_latest_summary(user_id=user_id, device_id=device_id)
        if summary is None or not summary.content.strip():
            return ""
        return "以下是更早历史对话的压缩摘要，回答时应保持一致：\n" + summary.content.strip()

    def compact_if_needed(
        self,
        *,
        user_id: str,
        device_id: str,
        threshold: int = 30,
        keep_latest: int = 5,
    ) -> MessageSummary | None:
        """按阈值压缩 active messages。

        主要逻辑：当 active 数量大于 threshold 时，把除最新 keep_latest 条外的旧消息
        写入 history 文件，追加一条 summary，然后重写 active 和 legacy messages。
        参数：`threshold` 为触发阈值，`keep_latest` 为压缩后保留最近消息数量。
        返回值：触发压缩时返回摘要记录，否则返回 None。
        异常情况：写文件失败时抛出底层 IO 异常；写入顺序保证先备份、再摘要、最后裁剪。
        """

        threshold = max(1, int(threshold or 30))
        keep_latest = max(1, int(keep_latest or 5))
        active = self.load_active_messages(user_id=user_id, device_id=device_id, limit=10_000)
        if len(active) <= threshold:
            return None
        archived = active[:-keep_latest]
        kept = active[-keep_latest:]
        if not archived:
            return None
        start_at = _message_timestamp(archived[0])
        end_at = _message_timestamp(archived[-1])
        marker = f"{_format_timestamp(start_at)}-{_format_timestamp(end_at)}"
        history_path = self._device_dir(user_id, device_id) / "history" / f"{marker}-messages.jsonl"
        history_rel = str(history_path.relative_to(self._device_dir(user_id, device_id)))
        self._write_jsonl(history_path, archived)
        summary = MessageSummary(
            summary_id=f"summary_{int(time.time() * 1000)}",
            user_id=user_id,
            device_id=device_id,
            source_message_count=len(archived),
            source_start_at=start_at,
            source_end_at=end_at,
            history_file=history_rel,
            content=self.summarizer.summarize(archived),
            created_at=time.time(),
        )
        self._append_jsonl(self._device_dir(user_id, device_id) / self.SUMMARY_FILE, asdict(summary))
        self._write_jsonl(self._device_dir(user_id, device_id) / self.ACTIVE_FILE, kept)
        self._write_jsonl(self._device_dir(user_id, device_id) / self.LEGACY_MESSAGES_FILE, kept)
        return summary

    def _device_dir(self, user_id: str, device_id: str) -> Path:
        path = self.root / _safe_path_part(user_id) / _safe_path_part(device_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _initialize_active_from_legacy(self, *, active_path: Path, legacy_path: Path) -> None:
        """从旧 messages.jsonl 初始化 active-messages.jsonl。

        主要逻辑：升级后的第一条新消息写入前也要执行迁移，否则新 active 文件会抢先
        创建，导致旧历史无法进入运行时上下文。
        参数：`active_path` 为 canonical 文件，`legacy_path` 为旧文件。
        返回值：无。
        异常情况：旧文件不存在或 active 已存在时不做处理。
        """

        if active_path.exists() or not legacy_path.exists():
            return
        self._write_jsonl(active_path, self._read_jsonl(legacy_path))

    @staticmethod
    def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            if isinstance(data, dict):
                records.append(data)
        return records


def _normalize_record(record: dict[str, Any], *, user_id: str, device_id: str) -> dict[str, Any]:
    normalized = dict(record)
    normalized.setdefault("user_id", user_id)
    normalized.setdefault("session_id", device_id)
    normalized.setdefault("device_id", device_id)
    normalized.setdefault("created_at", time.time())
    return normalized


def _message_timestamp(record: dict[str, Any]) -> float:
    for key in ("created_at", "updated_at", "timestamp_ms"):
        raw_value = record.get(key)
        if raw_value is None or raw_value == "":
            continue
        try:
            value = float(raw_value)
            if key == "timestamp_ms" and value > 1_000_000_000_000:
                value = value / 1000
            return value
        except Exception:
            continue
    return time.time()


def _format_timestamp(value: float) -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.localtime(value or time.time()))


def _compact_text(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.strip().split())
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _safe_path_part(value: str) -> str:
    text = str(value)
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text) or "_"
