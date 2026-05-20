from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from realtime_agent.agent_core.context import PromptRegistry
from realtime_agent.observability import LogContext, get_logger, log_error


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
                    {"role": "system", "content": _message_summary_prompt()},
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


def _message_summary_prompt() -> str:
    asset = PromptRegistry().maybe_get("message_summarizer")
    if asset is not None:
        return asset.content
    return (
        "你是会话历史摘要子Agent。你只输出中文结构化摘要，不输出JSON，不解释你的工作过程。\n"
        "你的任务是把 previous_summary 与 archived_messages 合并成一份更新后的滚动摘要。\n"
        "不要逐条复述聊天记录；要去重、归纳、保留会影响后续回答的事实、上下文和注意事项。\n"
        "视觉与环境线索只能作为历史观察记录，不能写成当前画面、当前图片或当前传感器状态。\n"
        "如果历史里出现看图、读图、拍照、相机超时、图片理解结果，只记录为过去某轮的结果或失败；后续新的视觉请求仍应依赖主Agent重新获取当前视觉输入。\n"
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
        if role == "assistant" and message.get("tool_calls"):
            result.append({"role": role, "content": _compact_tool_calls_for_summary(message.get("tool_calls"))})
            continue
        if role == "tool":
            result.append({"role": role, "content": _compact_tool_result_for_summary(message)})
            continue
        if role not in {"user", "assistant"}:
            continue
        content = _compact_text(message.get("content"), limit=1000)
        if content:
            result.append({"role": role, "content": content})
    return result


class ConversationMemoryService:
    """会话消息维护服务。

    主要功能：在内存中维护模型可见 active_messages，并用 `messages.jsonl` 保存
    当前 active 的完整离线备份，备份中包含工具调用过程。
    主要属性：`root` 是 runs 根目录；`_active_messages` 是大模型真正可见的进程内上文。
    """

    LEGACY_MESSAGES_FILE = "messages.jsonl"
    SUMMARY_FILE = "message-summaries.jsonl"

    def __init__(self, root: str | Path, *, summarizer: MessageSummarizer | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.summarizer = summarizer
        self.logger = get_logger("realtime_agent.runs")
        self._active_messages: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def append_message(self, *, user_id: str, device_id: str, message: dict[str, Any]) -> None:
        """追加一条会话消息。

        主要逻辑：`messages.jsonl` 保存当前 active 的完整离线备份，包括工具调用
        过程；进程内 active messages 只保存可直接进入模型上文的 user/assistant
        非空文本。
        参数：`user_id/device_id` 定位设备对话，`message` 为待保存消息。
        返回值：无。
        异常情况：文件不可写时抛出底层 IO 异常。
        """

        normalized = _normalize_record(message, user_id=user_id, device_id=device_id)
        legacy_path = self._device_dir(user_id, device_id) / self.LEGACY_MESSAGES_FILE
        active = self._ensure_active_messages(user_id=user_id, device_id=device_id)
        self._append_jsonl(legacy_path, normalized)
        if _is_model_visible_record(normalized):
            active.append(normalized)

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

        主要逻辑：读取进程内 active messages；首次访问时会从完整审计
        `messages.jsonl` 中筛选 user/assistant 非空文本作为重启恢复上下文。
        参数：`limit` 为最多返回条数。
        返回值：按原始顺序返回最近消息。
        异常情况：文件不存在时返回空列表。
        """

        if not user_id or not device_id or limit <= 0:
            return []
        records = self._ensure_active_messages(user_id=user_id, device_id=device_id)
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
        return (
            "以下是更早历史对话的压缩摘要，只用于保持用户偏好、任务背景和历史事实一致。"
            "其中的视觉与环境线索都是过去某轮的观察，不代表当前图片、当前画面或当前传感器状态；"
            "当用户提出新的看图、读图、眼前/前方观察请求时，应重新依赖当前可用视觉输入或视觉采集工具：\n"
            + summary.content.strip()
        )

    def compact_if_needed(
        self,
        *,
        user_id: str,
        device_id: str,
        threshold: int = 30,
        keep_latest: int = 5,
    ) -> MessageSummary | None:
        """按阈值压缩 active messages。

        主要逻辑：当内存 active 数量大于 threshold 时，把除最新 keep_latest 条外
        的旧消息压缩；被压缩的完整调用过程从 `messages.jsonl` 移入 history，
        `messages.jsonl` 重写为剩余 active 的完整备份，最后替换进程内 active。
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
        device_dir = self._device_dir(user_id, device_id)
        messages_path = device_dir / self.LEGACY_MESSAGES_FILE
        full_records = self._read_jsonl(messages_path)
        archived_full, kept_full = _split_records_by_visible_count(full_records, visible_count=len(archived))
        start_at = _message_timestamp(archived[0])
        end_at = _message_timestamp(archived[-1])
        marker = f"{_format_timestamp(start_at)}-{_format_timestamp(end_at)}"
        history_path = device_dir / "history" / f"{marker}-messages.jsonl"
        history_rel = str(history_path.relative_to(device_dir))
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
        self._write_jsonl(history_path, archived_full)
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
        self._append_jsonl(device_dir / self.SUMMARY_FILE, asdict(summary))
        self._write_jsonl(messages_path, kept_full)
        self._active_messages[(user_id, device_id)] = kept
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

    def _ensure_active_messages(self, *, user_id: str, device_id: str) -> list[dict[str, Any]]:
        """读取或初始化进程内 active messages。

        主要逻辑：active 是运行时状态，不落盘；如果当前进程还没有该用户设备的
        active，就从完整审计 `messages.jsonl` 筛选模型可见消息作为重启恢复上下文。
        参数：`user_id/device_id` 定位用户设备。
        返回值：可原地修改的 active messages 列表。
        异常情况：无。
        """

        key = (user_id, device_id)
        if key not in self._active_messages:
            legacy_path = self._device_dir(user_id, device_id) / self.LEGACY_MESSAGES_FILE
            self._active_messages[key] = _model_visible_records(self._read_jsonl(legacy_path))
        return self._active_messages[key]

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


def _is_model_visible_record(record: dict[str, Any]) -> bool:
    role = str(record.get("role") or "").strip()
    if role == "assistant":
        if _is_omni_realtime_tool_record(record):
            return False
        if _record_tool_calls(record):
            return True
        content = record.get("content")
        return isinstance(content, str) and bool(content.strip())
    if role == "tool":
        if _is_omni_realtime_tool_record(record):
            return False
        return bool(str(record.get("tool_call_id") or "").strip())
    if role == "user":
        content = record.get("content")
        return isinstance(content, str) and bool(content.strip())
    return False


def _model_visible_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    pending_tool_call_ids: set[str] = set()
    for record in records:
        role = str(record.get("role") or "").strip()
        if _is_omni_realtime_tool_record(record):
            continue
        if role == "assistant" and _record_tool_calls(record):
            visible.append(record)
            pending_tool_call_ids.update(_record_tool_call_ids(record))
            continue
        if role == "tool":
            tool_call_id = str(record.get("tool_call_id") or "").strip()
            if tool_call_id and tool_call_id in pending_tool_call_ids:
                visible.append(record)
                pending_tool_call_ids.discard(tool_call_id)
            continue
        if role in {"user", "assistant"} and _is_model_visible_record(record):
            visible.append(record)
    return visible


def _split_records_by_visible_count(
    records: list[dict[str, Any]],
    *,
    visible_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按模型可见消息数量切分完整消息记录。

    主要逻辑：`messages.jsonl` 中包含 tool 调用过程，不能简单按行数切分。这里按
    模型可见消息的数量确定边界，同时保留边界前的完整工具过程。
    参数：`records` 为完整 active 备份，`visible_count` 为要归档的模型可见消息数。
    返回值：`(archived_full, kept_full)`。
    异常情况：visible_count 小于等于 0 时不归档。
    """

    if visible_count <= 0:
        return [], list(records)
    archived: list[dict[str, Any]] = []
    visible_seen = 0
    split_index = 0
    for index, record in enumerate(records):
        archived.append(record)
        if _is_model_visible_record(record):
            visible_seen += 1
        if visible_seen >= visible_count:
            split_index = index + 1
            break
    else:
        split_index = len(records)
    return archived, records[split_index:]


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


def _record_tool_calls(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = record.get("tool_calls")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _record_tool_call_ids(record: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for item in _record_tool_calls(record):
        raw_id = item.get("id") or item.get("tool_call_id")
        if raw_id:
            ids.add(str(raw_id))
    return ids


def _is_omni_realtime_tool_record(record: dict[str, Any]) -> bool:
    source = str(record.get("source") or "").strip()
    return source == "omni_realtime" and (bool(_record_tool_calls(record)) or str(record.get("role") or "") == "tool")


def _compact_tool_calls_for_summary(value: Any) -> str:
    if not isinstance(value, list):
        return "调用工具：未知"
    parts = []
    for item in value:
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = str(item.get("name") or function.get("name") or "").strip()
        arguments = item.get("arguments")
        if arguments is None:
            arguments = function.get("arguments")
        arguments_text = json.dumps(arguments or {}, ensure_ascii=False, default=str)
        parts.append(f"{name or '未知工具'}({_compact_text(arguments_text, limit=300)})")
    return "调用工具：" + "；".join(parts) if parts else "调用工具：未知"


def _compact_tool_result_for_summary(message: dict[str, Any]) -> str:
    name = str(message.get("name") or "未知工具").strip()
    content = message.get("content")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str)
    return f"工具结果 {name}：{_compact_text(content, limit=1000)}"


def _safe_path_part(value: str) -> str:
    text = str(value)
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text) or "_"
