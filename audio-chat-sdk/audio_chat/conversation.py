from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from audio_chat.observability import LogContext, get_logger, log_error


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

    def summarize(self, *, previous_summary: str, messages: list[dict[str, Any]]) -> str:
        """生成摘要文本。"""


class ConversationSummaryError(RuntimeError):
    """会话摘要失败异常。

    主要功能：把 LLM 摘要配置缺失、依赖缺失、模型返回空内容或非法内容统一成
    可观测错误，供上层跳过本次压缩。
    """


class LlmMessageSummarizer:
    """基于 OpenAI-compatible Chat Completions 的会话摘要器。

    主要功能：用专用模型把 previous_summary 与本次归档消息合并成结构化滚动摘要。
    主要属性：`model` 是摘要模型名称，`base_url` 可指向 DashScope/OpenAI-compatible
    服务。
    """

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
        timeout_seconds: float = 5.0,
        max_retries: int = 1,
    ) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def summarize(self, *, previous_summary: str, messages: list[dict[str, Any]]) -> str:
        """调用模型生成结构化增量摘要。

        主要逻辑：把上一版摘要和本次归档消息交给摘要子 Agent，要求输出固定中文
        分区，禁止逐条复述原文。
        参数：`previous_summary` 为上一版摘要，`messages` 为本次即将归档的消息。
        返回值：结构化中文摘要。
        异常情况：配置缺失、依赖缺失、模型错误或返回空内容时抛出
        ConversationSummaryError。
        """

        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise ConversationSummaryError(f"{self.api_key_env} is required for message summarizer")
        if not self.model:
            raise ConversationSummaryError("message summarizer model is not configured")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ConversationSummaryError("openai package is required for message summarizer") from exc
        client = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            max_retries=self.max_retries,
        )
        try:
            completion = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _message_summary_system_prompt()},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "previous_summary": previous_summary or "",
                                "archived_messages": _messages_for_summary(messages),
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                temperature=0,
            )
        except Exception as exc:  # noqa: BLE001 - provider 异常需要转成统一摘要错误
            raise ConversationSummaryError(f"message summarizer provider failed: {exc}") from exc
        content = str(completion.choices[0].message.content or "").strip()
        if not content:
            raise ConversationSummaryError("message summarizer returned empty content")
        return content


def _message_summary_system_prompt() -> str:
    return (
        "你是会话历史摘要子Agent。你只输出中文结构化摘要，不输出JSON，不解释你的工作过程。\n"
        "你的任务是把 previous_summary 与 archived_messages 合并成一份更新后的滚动摘要。\n"
        "不要逐条复述聊天记录；要去重、归纳、保留会影响后续回答的事实、上下文和注意事项。\n"
        "如果 archived_messages 与 previous_summary 冲突，以较新的 archived_messages 为准，并在注意事项中说明。\n"
        "输出必须使用以下标题，标题顺序固定：\n"
        "用户身份与偏好：\n"
        "当前对话状态：\n"
        "视觉与环境线索：\n"
        "未完成事项与回答约束：\n"
        "每个标题下用 1-5 条短 bullet。只保留有用信息；没有内容时写“无”。"
    )


def _messages_for_summary(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue
        content = _compact_text(message.get("content"), limit=1000)
        if content:
            result.append({"role": role, "content": content})
    return result


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
        self.summarizer = summarizer
        self.logger = get_logger("audio_chat.runs")

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
        previous_summary = self.load_latest_summary(user_id=user_id, device_id=device_id)
        if self.summarizer is None:
            self._log_summary_failed(
                user_id=user_id,
                device_id=device_id,
                reason="message_summarizer_not_configured",
                error_type="ConversationSummaryError",
                error_message="message summarizer is not configured",
            )
            return None
        try:
            content = self.summarizer.summarize(
                previous_summary=previous_summary.content if previous_summary is not None else "",
                messages=archived,
            )
        except Exception as exc:  # noqa: BLE001 - 摘要失败必须跳过压缩并记录异常
            self._log_summary_failed(
                user_id=user_id,
                device_id=device_id,
                reason="message_summarizer_failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return None
        self._write_jsonl(history_path, archived)
        summary = MessageSummary(
            summary_id=f"summary_{int(time.time() * 1000)}",
            user_id=user_id,
            device_id=device_id,
            source_message_count=len(archived),
            source_start_at=start_at,
            source_end_at=end_at,
            history_file=history_rel,
            content=content,
            created_at=time.time(),
        )
        self._append_jsonl(self._device_dir(user_id, device_id) / self.SUMMARY_FILE, asdict(summary))
        self._write_jsonl(self._device_dir(user_id, device_id) / self.ACTIVE_FILE, kept)
        self._write_jsonl(self._device_dir(user_id, device_id) / self.LEGACY_MESSAGES_FILE, kept)
        return summary

    def _log_summary_failed(
        self,
        *,
        user_id: str,
        device_id: str,
        reason: str,
        error_type: str,
        error_message: str,
    ) -> None:
        """记录摘要失败并保持 active messages 不变。"""

        log_error(
            self.logger,
            "会话消息摘要失败，跳过本次压缩",
            LogContext(
                user_id=user_id,
                session_id=device_id,
                event="conversation.summary.failed",
                fields={
                    "reason": reason,
                    "error_type": error_type,
                    "error_message": error_message,
                    "detail_path": str(self._device_dir(user_id, device_id) / self.SUMMARY_FILE),
                },
            ),
        )

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
