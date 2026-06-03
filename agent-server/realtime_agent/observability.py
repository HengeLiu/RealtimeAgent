from __future__ import annotations

import json
import logging
import struct
import sys
import time
import wave
from ast import literal_eval
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from realtime_agent.protocol import Event, StreamChunk

NOISY_LIBRARY_LOG_LEVELS: tuple[tuple[str, int], ...] = (
    ("aiohttp.access", logging.WARNING),
    ("dashscope", logging.WARNING),
    ("httpcore", logging.WARNING),
    ("httpx", logging.WARNING),
    ("openai", logging.WARNING),
)
STANDARD_LOG_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
STORAGE_PATH_LOG_FIELDS = {"detail_path", "session_detail_path", "path"}
HIGH_FREQUENCY_AGENT_EVENTS = {
    "input_transcript.delta",
    "assistant_text.delta",
    "omni.conversation.item.input_audio_transcription.delta",
    "omni.response.audio.delta.decoded",
    "omni.response.audio_transcript.delta",
    "pipeline.output_audio_delta",
    "assistant_audio.delta",
}
QUIET_AGENT_TURN_STATE_REASONS = {
    "audio_delta",
    "input_audio_appended",
    "assistant_text_released",
    "audio_final_check",
}
QUIET_AGENT_EVENTS = {
    "context.source.added",
    "omni.input_audio.appended",
    "omni.response.audio.delta",
    "omni.response.function_call_arguments.delta",
    "omni.response.tool_call_arguments.delta",
    "vision.response_gate.buffered",
    "vision.response_gate.released",
}
DELTA_SUMMARY_DONE_EVENTS = {
    "assistant_audio.done": "assistant_audio.delta",
    "omni.response.audio.done": "omni.response.audio.delta.decoded",
    "pipeline.output_finished": "pipeline.output_audio_delta",
    "input_transcript.done": "input_transcript.delta",
    "omni.conversation.item.input_audio_transcription.completed": "omni.conversation.item.input_audio_transcription.delta",
    "omni.response.audio_transcript.done": "omni.response.audio_transcript.delta",
}
DELTA_DONE_TEXT_DUPLICATED_BY_MESSAGE_EVENTS = {
    "input_transcript.done",
    "omni.conversation.item.input_audio_transcription.completed",
    "omni.response.audio_transcript.done",
}
QUIET_CONTROL_EVENTS = {"control.device.heartbeat.received"}
QUIET_CONTROL_PREFIXES = ("stream.",)
HIGH_FREQUENCY_CONTROL_PROGRESS_STATUSES = {"peer.video.frame_processed"}
DEBUG_ONLY_CONTROL_PREFIXES = ("stream.",)
VISIBLE_STREAM_EVENTS = {
    "stream.opened",
    "stream.closed",
    "stream.failed",
    "stream.output.summary",
}


@dataclass(frozen=True)
class LogContext:
    """终端日志上下文。

    主要功能：统一传递 `user_id`、`session_id`、`device_id`、`stream_id` 等链路字段。
    主要属性：`fields` 保存额外业务字段，格式化器会以 `key=value` 形式追加到日志末尾。
    """

    user_id: str | None = None
    session_id: str | None = None
    device_id: str | None = None
    stream_id: str | None = None
    event: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为 logging extra 字段。"""

        result: dict[str, Any] = {}
        for key in ("user_id", "session_id", "device_id", "stream_id", "event"):
            value = getattr(self, key)
            if value and key not in STANDARD_LOG_RECORD_FIELDS:
                result[key] = value
        for key, value in self.fields.items():
            if value is not None and key not in STANDARD_LOG_RECORD_FIELDS and key not in STORAGE_PATH_LOG_FIELDS:
                result[key] = value
        return result


class LineFormatter(logging.Formatter):
    """开发调试用单行日志格式化器。"""

    def __init__(self, *, timezone_name: str = "local") -> None:
        """创建单行日志格式化器。

        主要逻辑：默认使用服务器本地时区；配置为 IANA 时区名时使用指定时区，
        例如 `Asia/Shanghai`，避免 Linux 服务器系统时区不是北京时间时日志难读。
        参数：`timezone_name` 为 `local`/`auto`/空字符串或 IANA 时区名。
        返回值：无。
        异常情况：未知时区会回退到本地时区。
        """

        super().__init__()
        self.timezone_name = str(timezone_name or "local")
        self.timezone = _resolve_log_timezone(self.timezone_name)

    def format(self, record: logging.LogRecord) -> str:
        """格式化为 `time level logger message key=value`。"""

        timestamp = datetime.now(tz=self.timezone).isoformat(timespec="milliseconds")
        message = record.getMessage()
        line = f"{timestamp} {record.levelname} {record.name} {message}"
        standard = STANDARD_LOG_RECORD_FIELDS | {"exc_info", "exc_text", "stack_info"}
        fields: list[str] = []
        for key, value in sorted(record.__dict__.items()):
            if key in standard or key in STORAGE_PATH_LOG_FIELDS or key.startswith("_"):
                continue
            fields.append(f"{key}={_format_log_value(value)}")
        if fields:
            line = f"{line} {' '.join(fields)}"
        return line


def configure_console_logging(level: str = "INFO", *, timezone_name: str = "local") -> None:
    """配置开发终端日志。

    主要逻辑：绑定 stdout 单行日志格式，并压低第三方库的噪声日志。
    参数：`level` 为 DEBUG/INFO/WARNING/ERROR 等标准级别名称；`timezone_name`
    为日志时间时区，默认 `local` 使用系统本地时区，可设置为 `Asia/Shanghai`。
    返回值：无。
    异常情况：未知级别时回退 INFO。
    """

    resolved = getattr(logging, str(level or "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(resolved)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(LineFormatter(timezone_name=timezone_name))
    root.addHandler(handler)
    for name, logger_level in NOISY_LIBRARY_LOG_LEVELS:
        logging.getLogger(name).setLevel(logger_level)


def _resolve_log_timezone(timezone_name: str):
    """解析日志时区配置。

    主要逻辑：`local`、`auto` 或空值走系统本地时区；其他值按 IANA 时区名解析。
    参数：`timezone_name` 是配置中的时区名。
    返回值：tzinfo 对象。
    异常情况：未知时区回退系统本地时区。
    """

    name = str(timezone_name or "local").strip()
    if not name or name.lower() in {"local", "auto", "system"}:
        return datetime.now().astimezone().tzinfo
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return datetime.now().astimezone().tzinfo


def should_log_control_event_to_terminal(event: Event) -> bool:
    """判断控制事件是否需要出现在终端日志。

    主要逻辑：控制事件仍完整写入 jsonl；终端只保留低频或关键事件。逐帧视觉进度
    这类高频事件会刷掉有效信息，因此仅落盘，不在终端输出。
    参数：`event` 为控制事件。
    返回值：需要输出终端日志时返回 True。
    异常情况：无。
    """

    if event.event_name in QUIET_CONTROL_EVENTS:
        return False
    if event.event_name.startswith(QUIET_CONTROL_PREFIXES):
        return False
    return not _is_high_frequency_control_progress(event)


def get_logger(name: str) -> logging.Logger:
    """获取命名日志器。"""

    return logging.getLogger(name)


def log_debug(logger: logging.Logger, message: str, context: LogContext | None = None) -> None:
    """输出 DEBUG 日志。"""

    logger.debug(message, extra=context.to_dict() if context else {})


def log_info(logger: logging.Logger, message: str, context: LogContext | None = None) -> None:
    """输出 INFO 日志。"""

    logger.info(message, extra=context.to_dict() if context else {})


def log_warning(logger: logging.Logger, message: str, context: LogContext | None = None) -> None:
    """输出 WARNING 日志。"""

    logger.warning(message, extra=context.to_dict() if context else {})


def log_error(logger: logging.Logger, message: str, context: LogContext | None = None) -> None:
    """输出 ERROR 日志。"""

    logger.error(message, extra=context.to_dict() if context else {})


def _format_log_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


class RunRecorder:
    """运行产物记录器。

    主要功能：把控制事件、流事件、Agent 事件、模型请求、工具调用和媒体文件
    写入稳定目录，并输出适合开发终端阅读的摘要日志。
    主要方法：`record_event()` 记录控制事件，`record_model_request()` 保存模型请求，
    `record_stream_event()` 汇总数据流生命周期，`record_message()` 保存会话消息。
    主要属性：`runs_root` 是运行产物根目录，`logger` 是统一的运行日志器。
    """

    def __init__(self, runs_root: str | Path = "runs/default-app") -> None:
        self.runs_root = Path(runs_root).expanduser().resolve()
        self.logger = get_logger("realtime_agent.runs")
        self._session_users: dict[str, str] = {}
        self._stream_chunk_stats: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._provider_input_audio_stats: dict[tuple[str, str], dict[str, Any]] = {}
        self._agent_event_counts: dict[tuple[str, str], int] = {}
        self._model_request_started_at: dict[str, float] = {}
        self._delta_stats: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._turn_timeline: dict[str, dict[str, Any]] = {}
        self._first_model_request_logged = False
        self._artifact_index_logged = False
        self._log_artifact_index_once()

    def _log_artifact_index_once(self) -> None:
        """在进程启动阶段打印一次运行产物目录索引。

        主要逻辑：终端后续事件不再重复打印 `detail_path/path`，因此启动时集中给出
        根目录、会话目录和常见文件含义，便于开发者按事件类型定位落盘细节。
        参数：无。
        返回值：无。
        异常情况：无。
        """

        if self._artifact_index_logged:
            return
        self._artifact_index_logged = True
        log_info(
            self.logger,
            "运行产物目录索引",
            LogContext(
                fields={
                    "runs_root": str(self.runs_root),
                    "session_dir_pattern": "<runs_root>/<user_id>/<device_id>/",
                    "media_dir_pattern": "<runs_root>/<user_id>/<device_id>/<audio|photos|imu|depth|assets>/",
                    "global_files": {
                        "control-events.jsonl": "全局控制事件流水",
                        "control-routes.jsonl": "控制事件订阅匹配和投递结果",
                        "system-events.jsonl": "系统错误、降级和恢复事件",
                        "capability-events.jsonl": "跨会话能力调用轨迹",
                        "command-events.jsonl": "跨会话设备命令轨迹",
                        "debug/playback.json": "当前播放仲裁快照",
                    },
                    "session_files": {
                        "events.jsonl": "当前设备会话控制事件",
                        "messages.jsonl": "用户、助手和工具消息历史",
                        "model-request.json": "最近一次发给模型的请求快照",
                        "agent-events.jsonl": "Agent Core、模型 provider 和 delta 摘要事件",
                        "model-events.jsonl": "模型相关事件镜像，便于按模型视角排查",
                        "tool-events.jsonl": "工具调用参数、结果、耗时和错误",
                        "stream-events.jsonl": "数据流打开、关闭、失败和分片摘要",
                        "assets.jsonl": "图片等资产写入和请求记录",
                        "task-signals.jsonl": "Task Engine 信号记录",
                        "output-decisions.jsonl": "服务端输出仲裁决策",
                        "playback-decisions.jsonl": "端侧播放仲裁决策",
                        "actuators.jsonl": "端侧执行器播放和回执记录",
                    },
                    "user_files": {"memory.json": "用户长期记忆"},
                    "task_dir_pattern": "<runs_root>/tasks/",
                },
            ),
        )

    def bind_device(self, *, user_id: str, device_id: str) -> Path:
        """绑定当前运行目录中的用户和设备。

        主要逻辑：新版运行产物以 device_id 作为 session_id，本方法把二者绑定到
        `runs/<app_name>/<user_id>/<device_id>/`，并创建最关键的 `messages.jsonl`
        和 `model-request.json` 占位文件，避免开发者排障时找不到入口。
        参数：`user_id` 为用户标识，`device_id` 为设备标识。
        返回值：设备级运行目录。
        异常情况：文件系统不可写时抛出底层 IO 异常。
        """

        if not user_id or not device_id:
            raise ValueError("bind_device requires user_id and device_id")
        self._session_users[device_id] = user_id
        path = self._device_dir(user_id, device_id)
        messages = path / "messages.jsonl"
        messages.touch(exist_ok=True)
        model_request = path / "model-request.json"
        if not model_request.exists():
            model_request.write_text(
                json.dumps(
                    {
                        "status": "not_started",
                        "user_id": user_id,
                        "session_id": device_id,
                        "device_id": device_id,
                        "note": "model request has not been created yet",
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        return path

    def session_dir(self, session_id: str, user_id: str | None = None) -> Path:
        if user_id:
            self._session_users[session_id] = user_id
        bound_user_id = user_id or self._session_users.get(session_id)
        if bound_user_id:
            return self.bind_device(user_id=bound_user_id, device_id=session_id)
        path = self.runs_root / "_unbound" / self._safe_path_part(session_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def user_dir(self, user_id: str) -> Path:
        path = self.runs_root / self._safe_path_part(user_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def session_file(self, session_id: str, filename: str) -> Path:
        return self.session_dir(session_id) / filename

    def _event_detail_path(self, event: Event) -> str:
        if event.session_id:
            return str(self.session_file(event.session_id, "events.jsonl"))
        return str(self.runs_root / "control-events.jsonl")

    def record_event(self, event: Event) -> None:
        if event.session_id:
            self._append_jsonl(self.session_dir(event.session_id, user_id=event.user_id) / "events.jsonl", event.to_dict())
        self._append_jsonl(self.runs_root / "control-events.jsonl", event.to_dict())
        if not should_log_control_event_to_terminal(event):
            return
        payload = event.payload or {}
        error_fields = _error_log_fields(payload.get("provider_error_message") or payload.get("message") or payload.get("error"))
        error_message = _control_event_error_message(event, payload, error_fields)
        if event.event_name.startswith("system.error"):
            level_logger = log_warning if payload.get("severity") == "warning" else log_error
        elif event.event_name.startswith(DEBUG_ONLY_CONTROL_PREFIXES):
            level_logger = log_debug
        else:
            level_logger = log_info
        level_logger(
            self.logger,
            f"控制事件 {event.event_name}",
            LogContext(
                user_id=event.user_id,
                session_id=event.session_id,
                device_id=event.producer_id,
                stream_id=event.stream_id,
                event=event.event_name,
                fields={
                    "stream_type": event.stream_type,
                    "status": payload.get("status"),
                    "status_message": _compact_text(payload.get("message"), limit=200) if error_message is None else None,
                    "reason": payload.get("reason"),
                    "error_type": payload.get("error_type"),
                    "error_message": error_message,
                    "provider_error_code": error_fields.get("provider_error_code") or payload.get("provider_error_code"),
                    "provider_error_type": error_fields.get("provider_error_type") or payload.get("provider_error_type"),
                    "provider_event_id": error_fields.get("provider_event_id") or payload.get("provider_event_id"),
                    "suppressed_count": payload.get("suppressed_count"),
                    "detail_path": self._event_detail_path(event),
                },
            ),
        )

    def record_event_route(self, event: Event, record: dict[str, Any]) -> None:
        """记录控制事件订阅匹配和投递诊断。

        主要逻辑：写入全局 `control-routes.jsonl`，有 session 时同步写入会话目录。
        终端只用 DEBUG 打一行摘要，避免正常 stream 或心跳链路刷屏。
        参数：`event` 为原始控制事件，`record` 为 Control Service 生成的路由摘要。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """

        payload = {
            "timestamp_ms": event.timestamp_ms,
            "user_id": event.user_id,
            "session_id": event.session_id,
            "producer_id": event.producer_id,
            "stream_id": event.stream_id,
            "stream_type": event.stream_type,
            **dict(record),
        }
        self._append_jsonl(self.runs_root / "control-routes.jsonl", payload)
        if event.session_id:
            self._append_jsonl(self.session_dir(event.session_id, user_id=event.user_id) / "control-routes.jsonl", payload)
        if not should_log_control_event_to_terminal(event):
            return
        detail_path = str(self.session_file(event.session_id, "control-routes.jsonl")) if event.session_id else str(self.runs_root / "control-routes.jsonl")
        log_debug(
            self.logger,
            f"事件路由 {event.event_name}",
            LogContext(
                user_id=event.user_id,
                session_id=event.session_id,
                device_id=event.producer_id,
                stream_id=event.stream_id,
                event="event.route.resolved",
                fields={
                    "matched_count": record.get("matched_count"),
                    "delivered_count": record.get("delivered_count"),
                    "stream_type": event.stream_type,
                    "detail_path": detail_path,
                },
            ),
        )

    def record_stream_event(self, session_id: str, record: dict[str, Any]) -> None:
        """记录 stream 事件，并控制终端日志噪音。

        主要逻辑：所有事件仍写入 `stream-events.jsonl` 便于复盘；终端只打印
        stream 的开始、结束、失败和输出摘要，避免高频 chunk 或晚到丢弃事件刷屏。
        参数：`session_id` 为会话编号，`record` 为 stream 事件内容。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """

        self._bind_from_record(session_id, record)
        self._append_jsonl(self.session_dir(session_id) / "stream-events.jsonl", record)
        name = str(record.get("event") or "")
        stream_id = str(record.get("stream_id") or "")
        stream_type = str(record.get("stream_type") or "")
        if name in {"stream.chunk.received", "stream.chunk.sent"}:
            key = (session_id, stream_id, name)
            now = time.monotonic()
            stats = self._stream_chunk_stats.setdefault(
                key,
                {"count": 0, "bytes": 0, "first_seq": record.get("seq"), "last_seq": record.get("seq"), "started_at": now, "last_at": now},
            )
            stats["count"] = int(stats.get("count") or 0) + 1
            stats["bytes"] = int(stats.get("bytes") or 0) + int(record.get("payload_size") or 0)
            stats["last_seq"] = record.get("seq")
            stats["last_at"] = now
            if name == "stream.chunk.received" and stream_type == "sensor.mic":
                if stats["count"] == 1:
                    self.record_timeline_checkpoint(
                        session_id,
                        checkpoint="text.timeline.audio.first_chunk_received",
                        user_id=str(record.get("user_id") or ""),
                        stream_id=stream_id,
                        fields={
                            "seq": record.get("seq"),
                            "payload_size": record.get("payload_size"),
                            "stream_type": stream_type,
                        },
                    )
                if record.get("final"):
                    self.record_timeline_checkpoint(
                        session_id,
                        checkpoint="text.timeline.audio.input_done",
                        user_id=str(record.get("user_id") or ""),
                        stream_id=stream_id,
                        fields={
                            "seq": record.get("seq"),
                            "payload_size": record.get("payload_size"),
                            "stream_type": stream_type,
                            "input_chunk_count": stats.get("count"),
                            "input_bytes": stats.get("bytes"),
                            "input_duration_ms": _elapsed_ms(
                                float(stats.get("started_at") or now),
                                float(stats.get("last_at") or now),
                            ),
                        },
                    )
            return
        if name not in VISIBLE_STREAM_EVENTS:
            return
        chunk_fields = self._pop_stream_chunk_summary(session_id=session_id, stream_id=stream_id)
        log_info(
            self.logger,
            f"数据流事件 {name}",
            LogContext(
                session_id=session_id,
                stream_id=stream_id,
                event=name,
                fields={
                    "stream_type": stream_type,
                    "producer_id": record.get("producer_id"),
                    "consumer_device_ids": record.get("consumer_device_ids"),
                    "reason": record.get("reason"),
                    "bytes": record.get("payload_size"),
                    **chunk_fields,
                    "detail_path": str(self.session_file(session_id, "stream-events.jsonl")),
                },
            ),
        )

    def record_agent_event(self, session_id: str, record: dict[str, Any]) -> None:
        self._bind_from_record(session_id, record)
        self._append_jsonl(self.session_dir(session_id) / "agent-events.jsonl", record)
        self._append_jsonl(self.session_dir(session_id) / "model-events.jsonl", record)
        event = str(record.get("event") or "")
        error_fields = _error_log_fields(
            record.get("provider_error_message") or record.get("message") or record.get("error") or record.get("raw")
        )
        context = LogContext(
            user_id=record.get("user_id"),
            session_id=session_id,
            event=event,
            fields={
                "provider": record.get("provider"),
                "model": record.get("model"),
                "agent_core": record.get("agent_core"),
                "tool_name": record.get("tool_name"),
                "tool_call_id": record.get("tool_call_id") or record.get("call_id"),
                "ok": record.get("ok"),
                "status": record.get("status"),
                "reason": record.get("reason"),
                "error_message": error_fields.get("error_message")
                or _compact_text(record.get("message") or record.get("error"), limit=1000),
                "provider_error_code": record.get("provider_error_code") or error_fields.get("provider_error_code"),
                "provider_error_type": record.get("provider_error_type") or error_fields.get("provider_error_type"),
                "provider_event_id": record.get("provider_event_id") or error_fields.get("provider_event_id"),
                "websocket_code": record.get("code") or record.get("websocket_code"),
                "websocket_message": _compact_text(record.get("message") if event.endswith("websocket.closed") else record.get("websocket_message"), limit=500),
                "bytes": record.get("audio_bytes") or record.get("payload_size"),
                "image_bytes": record.get("image_bytes"),
                "image_sha256": record.get("image_sha256"),
                "replayed_audio_bytes": record.get("replayed_audio_bytes"),
                "replayed_audio_chunk_count": record.get("replayed_audio_chunk_count"),
                "final": record.get("final"),
                "text": _compact_text(record.get("text")),
                "detail_path": str(self.session_file(session_id, "agent-events.jsonl")),
            },
        )
        if event in QUIET_AGENT_EVENTS:
            return
        if event in HIGH_FREQUENCY_AGENT_EVENTS:
            self._record_delta_summary_start(session_id=session_id, event=event, record=record, context=context)
            return
        if event in DELTA_SUMMARY_DONE_EVENTS:
            self._record_delta_summary_done(session_id=session_id, done_event=event, record=record, context=context)
            return
        if _is_quiet_agent_turn_state(record):
            return
        if event == "omni.provider_input_audio.saved" and not record.get("final") and int(record.get("chunk_count") or 0) != 1:
            return
        log_info(self.logger, f"Agent事件 {event}", context)

    def record_conversation_event(self, session_id: str, record: dict[str, Any]) -> None:
        """记录 conversation runtime 事件。

        主要逻辑：conversation 事件写入独立 `conversation-events.jsonl`，便于复盘
        VAD、turn 和打断控制；同时保留一份到 `agent-events.jsonl`，兼容现有排障
        入口和测试。
        参数：`session_id` 定位会话；`record` 是可 JSON 序列化事件记录。
        返回值：无。
        异常情况：文件写入异常按底层 `_append_jsonl` 行为抛出。
        """

        self._bind_from_record(session_id, record)
        self._append_jsonl(self.session_dir(session_id) / "conversation-events.jsonl", record)
        self._append_jsonl(self.session_dir(session_id) / "agent-events.jsonl", record)

    def record_timeline_checkpoint(
        self,
        session_id: str,
        *,
        checkpoint: str,
        user_id: str | None = None,
        stream_id: str | None = None,
        fields: dict[str, Any] | None = None,
        repeat: bool = False,
    ) -> None:
        """记录 Text 实时链路的关键时间点。

        主要逻辑：把用户关心的端到端时间点同时写入 `agent-events.jsonl` /
        `model-events.jsonl` 并打印到终端。默认同一 checkpoint 每轮只记录一次，
        避免高频 delta 刷屏。
        参数：`checkpoint` 为时间点事件名；`fields` 是该时间点的补充摘要。
        返回值：无。
        异常情况：文件写入失败时抛出底层 IO 异常。
        """

        if not session_id:
            return
        now = time.monotonic()
        timeline = self._turn_timeline.setdefault(session_id, {"checkpoints": {}, "last_at": None})
        checkpoints: dict[str, float] = timeline.setdefault("checkpoints", {})
        if not repeat and checkpoint in checkpoints:
            return
        previous_at = timeline.get("last_at")
        checkpoints[checkpoint] = now
        timeline["last_at"] = now
        base_at = checkpoints.get("text.timeline.audio.first_chunk_received")
        wall_time = datetime.now().astimezone().isoformat(timespec="milliseconds")
        extra = dict(fields or {})
        record = {
            "event": checkpoint,
            "checkpoint": checkpoint,
            "user_id": user_id,
            "stream_id": stream_id,
            "at": wall_time,
            "since_audio_start_ms": _elapsed_ms(base_at, now),
            "since_previous_checkpoint_ms": _elapsed_ms(previous_at, now),
            **extra,
        }
        self._bind_from_record(session_id, record)
        self._append_jsonl(self.session_dir(session_id) / "agent-events.jsonl", record)
        self._append_jsonl(self.session_dir(session_id) / "model-events.jsonl", record)
        log_info(
            self.logger,
            f"实时链路时间点 {checkpoint}",
            LogContext(
                user_id=user_id,
                session_id=session_id,
                stream_id=stream_id,
                event=checkpoint,
                fields={
                    "at": wall_time,
                    "since_audio_start_ms": record.get("since_audio_start_ms"),
                    "since_previous_checkpoint_ms": record.get("since_previous_checkpoint_ms"),
                    **extra,
                    "detail_path": str(self.session_file(session_id, "agent-events.jsonl")),
                },
            ),
        )
        if checkpoint == "text.timeline.tts.audio_done":
            self._record_timeline_summary(session_id=session_id, user_id=user_id)

    def _record_timeline_summary(self, *, session_id: str, user_id: str | None) -> None:
        """打印 Text 实时链路关键时间点汇总。"""

        timeline = self._turn_timeline.get(session_id) or {}
        checkpoints: dict[str, float] = dict(timeline.get("checkpoints") or {})
        base_at = checkpoints.get("text.timeline.audio.first_chunk_received")
        if base_at is None:
            return
        names = [
            "text.timeline.audio.first_chunk_received",
            "text.timeline.audio.input_done",
            "text.timeline.asr.first_char",
            "text.timeline.asr.done",
            "text.timeline.llm.first_token",
            "text.timeline.llm.done",
            "text.timeline.tts.first_audio_chunk",
            "text.timeline.tts.audio_done",
        ]
        offsets = {name: _elapsed_ms(base_at, checkpoints.get(name)) for name in names}
        record = {
            "event": "text.timeline.summary",
            "user_id": user_id,
            "offsets_ms": offsets,
        }
        self._append_jsonl(self.session_dir(session_id) / "agent-events.jsonl", record)
        self._append_jsonl(self.session_dir(session_id) / "model-events.jsonl", record)
        log_info(
            self.logger,
            "实时链路时间线汇总",
            LogContext(
                user_id=user_id,
                session_id=session_id,
                event="text.timeline.summary",
                fields={**offsets, "detail_path": str(self.session_file(session_id, "agent-events.jsonl"))},
            ),
        )

    def record_tool_trace(self, session_id: str, record: dict[str, Any]) -> None:
        """记录 Tool 调用轨迹。

        主要逻辑：写入稳定 `tool-events.jsonl`，供回放和排障使用。
        参数：`session_id` 为会话，`record` 为工具调用结构。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        self._bind_from_record(session_id, record)
        self._append_jsonl(self.session_dir(session_id) / "tool-events.jsonl", record)
        log_info(
            self.logger,
            f"工具调用 {record.get('tool_name')}",
            LogContext(
                user_id=record.get("user_id"),
                session_id=session_id,
                event="tool.trace",
                fields={
                    "ok": record.get("ok"),
                    "duration_ms": record.get("duration_ms"),
                    "error": record.get("error"),
                    "detail_path": str(self.session_file(session_id, "tool-events.jsonl")),
                },
            ),
        )

    def record_task_signal(self, session_id: str, record: dict[str, Any]) -> None:
        """记录 TaskSignal。

        主要逻辑：写入 `task-signals.jsonl`。
        参数：`session_id` 为会话或任务标识，`record` 为任务信号结构。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        self._bind_from_record(session_id, record)
        self._append_jsonl(self.session_dir(session_id) / "task-signals.jsonl", record)
        if _is_quiet_task_signal(record):
            return
        log_info(
            self.logger,
            f"任务信号 {record.get('signal_name')}",
            LogContext(
                session_id=session_id,
                event=record.get("signal_name"),
                fields={**record, "detail_path": str(self.session_file(session_id, "task-signals.jsonl"))},
            ),
        )

    def record_asset_event(self, session_id: str, record: dict[str, Any]) -> None:
        """记录 Asset Service 事件。

        主要逻辑：写入 `assets.jsonl`，让回放产物能直接解释资产写入、request_id
        和 stream 类型。
        参数：`session_id` 为会话，`record` 为资产事件。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        self._bind_from_record(session_id, record)
        self._append_jsonl(self.session_dir(session_id) / "assets.jsonl", record)
        log_info(
            self.logger,
            f"资产事件 {record.get('event')}",
            LogContext(
                session_id=session_id,
                event=record.get("event"),
                fields={
                    "asset_id": record.get("asset_id"),
                    "request_id": record.get("request_id"),
                    "stream_type": record.get("stream_type"),
                    "matched_count": record.get("matched_count"),
                    "delivered_count": record.get("delivered_count"),
                    "timeout_seconds": record.get("timeout_seconds"),
                    "detail_path": str(self.session_file(session_id, "assets.jsonl")),
                },
            ),
        )

    def record_model_request(self, session_id: str, record: dict[str, Any]) -> None:
        """记录模型请求。

        主要逻辑：写入 `model-request.json`，保留一轮交互发给模型的稳定请求快照。
        参数：`session_id` 为会话，`record` 为模型请求。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        self._bind_from_record(session_id, record)
        path = self.session_dir(session_id) / "model-request.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        self._model_request_started_at[session_id] = time.monotonic()
        if not self._first_model_request_logged:
            self._first_model_request_logged = True
            self._log_first_model_request(session_id=session_id, record=record)
        log_info(
            self.logger,
            "模型请求已写入",
            LogContext(
                user_id=record.get("user_id"),
                session_id=session_id,
                event="model.request",
                fields={
                    "provider": record.get("provider"),
                    "model": record.get("model"),
                    "runner": record.get("runner"),
                    "message_count": len(record.get("messages") or []),
                    "tool_count": record.get("tool_count", len(record.get("tools") or [])),
                    "path": str(path),
                    "detail_path": str(path),
                },
            ),
        )

    def _log_first_model_request(self, *, session_id: str, record: dict[str, Any]) -> None:
        """在进程首次调用大模型前打印完整请求快照。

        主要逻辑：首次 `record_model_request()` 发生在 provider 调用之前，此处把同一份
        请求整理成“模型可见上下文”后打印，方便开发者先看 system prompt、messages
        和 tools；provider 原始字段和中间调试字段仍完整落盘到 `model-request.json`。
        参数：`session_id` 为当前会话，`record` 为模型请求快照。
        返回值：无。
        异常情况：JSON 序列化异常时退回 `str(record)`。
        """

        log_record = _model_request_terminal_snapshot(record)
        try:
            snapshot = json.dumps(log_record, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception:
            snapshot = str(log_record)
        self.logger.info(
            "首次模型请求完整快照\n%s",
            snapshot,
            extra=LogContext(
                user_id=record.get("user_id"),
                session_id=session_id,
                event="model.request.first",
                fields={
                    "provider": record.get("provider"),
                    "model": record.get("model"),
                    "runner": record.get("runner"),
                    "message_count": len(record.get("messages") or []),
                    "tool_count": record.get("tool_count", len(record.get("tools") or [])),
                    "detail_path": str(self.session_file(session_id, "model-request.json")),
                },
            ).to_dict(),
        )

    def write_result(self, session_id: str, record: dict[str, Any]) -> None:
        """写入会话结果。

        主要逻辑：输出稳定 `result.json`，作为回放断言入口。
        参数：`session_id` 为会话，`record` 为结果。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        self._bind_from_record(session_id, record)
        path = self.session_dir(session_id) / "result.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def record_playback_result(self, session_id: str, record: dict[str, Any]) -> None:
        self._bind_from_record(session_id, record)
        path = self.session_dir(session_id) / "playback-result.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def record_system_event(self, record: dict[str, Any]) -> None:
        self._append_jsonl(self.runs_root / "system-events.jsonl", record)
        event_name = record.get("event") or record.get("event_name")
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        error_fields = _error_log_fields(
            record.get("provider_error_message")
            or payload.get("provider_error_message")
            or record.get("message")
            or payload.get("message")
            or record.get("error")
            or payload.get("error")
            or payload
        )
        severity = str(record.get("severity") or payload.get("severity") or "error")
        if severity == "warning":
            log_method = log_warning
        elif severity in {"debug", "info"}:
            log_method = log_debug if severity == "debug" else log_info
        else:
            log_method = log_error
        log_method(
            self.logger,
            f"系统事件 {event_name}",
            LogContext(
                user_id=record.get("user_id"),
                session_id=record.get("session_id"),
                device_id=record.get("device_id") or payload.get("device_id") or record.get("producer_id"),
                stream_id=record.get("stream_id"),
                event=event_name,
                fields={
                    "component": record.get("component") or payload.get("component"),
                    "reason": record.get("reason") or payload.get("reason"),
                    "error_type": record.get("error_type") or payload.get("error_type"),
                    "error_message": error_fields.get("error_message")
                    or _compact_text(record.get("message") or payload.get("message"), limit=1000),
                    "provider_error_code": record.get("provider_error_code")
                    or payload.get("provider_error_code")
                    or error_fields.get("provider_error_code"),
                    "provider_error_type": record.get("provider_error_type")
                    or payload.get("provider_error_type")
                    or error_fields.get("provider_error_type"),
                    "provider_event_id": record.get("provider_event_id")
                    or payload.get("provider_event_id")
                    or error_fields.get("provider_event_id"),
                    "call_id": record.get("call_id") or payload.get("call_id"),
                    "suppressed_count": payload.get("suppressed_count"),
                    "stream_type": record.get("stream_type"),
                    "detail_path": str(self.runs_root / "system-events.jsonl"),
                    "session_detail_path": str(self.session_file(str(record.get("session_id")), "events.jsonl")) if record.get("session_id") else None,
                },
            ),
        )

    def record_capability_trace(self, session_id: str, record: dict[str, Any]) -> None:
        """记录能力 API 调用级轨迹。"""

        self._bind_from_record(session_id, record)
        self._append_jsonl(self.session_dir(session_id) / "capability-events.jsonl", record)
        self._append_jsonl(self.runs_root / "capability-events.jsonl", record)
        log_info(
            self.logger,
            f"能力调用 {record.get('capability')}",
            LogContext(
                user_id=record.get("user_id"),
                session_id=session_id,
                event=record.get("event"),
                fields={
                    "capability": record.get("capability"),
                    "matched_count": record.get("matched_count"),
                    "result": record.get("result"),
                    "detail_path": str(self.session_file(session_id, "capability-events.jsonl")),
                },
            ),
        )

    def record_command_trace(self, session_id: str, record: dict[str, Any]) -> None:
        """记录设备命令 API 调用级轨迹。"""

        self._bind_from_record(session_id, record)
        self._append_jsonl(self.session_dir(session_id) / "command-events.jsonl", record)
        self._append_jsonl(self.runs_root / "command-events.jsonl", record)
        log_info(
            self.logger,
            f"设备命令 {record.get('command')}",
            LogContext(
                user_id=record.get("user_id"),
                session_id=session_id,
                event=record.get("event"),
                fields={
                    "command_id": record.get("command_id"),
                    "ok": record.get("ok"),
                    "device_count": record.get("device_count"),
                    "error_count": record.get("error_count"),
                    "detail_path": str(self.session_file(session_id, "command-events.jsonl")),
                },
            ),
        )

    def record_playback_decision(self, session_id: str, record: dict[str, Any]) -> None:
        self._bind_from_record(session_id, record)
        self._append_jsonl(self.session_dir(session_id) / "output-decisions.jsonl", record)
        self._append_jsonl(self.session_dir(session_id) / "playback-decisions.jsonl", record)
        log_info(
            self.logger,
            f"播放决策 {record.get('action')}",
            LogContext(
                user_id=record.get("user_id"),
                session_id=session_id,
                event="playback.decision",
                fields={
                    "reason": record.get("reason"),
                    "priority": record.get("priority"),
                    "active_stream_id": record.get("active_stream_id"),
                    "detail_path": str(self.session_file(session_id, "playback-decisions.jsonl")),
                },
            ),
        )

    def record_actuator_event(self, session_id: str, record: dict[str, Any]) -> None:
        """记录端侧执行器回放事件。

        主要逻辑：把 playback 端点收到的 speaker / haptic 输出、播放回执和保存路径
        写入 `actuators.jsonl`，让设备级回放能解释 server 下发内容是否被端侧消费。
        参数：`session_id` 为会话，`record` 为执行器事件。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """

        self._bind_from_record(session_id, record)
        self._append_jsonl(self.session_dir(session_id) / "actuators.jsonl", record)
        log_info(
            self.logger,
            f"执行器事件 {record.get('event')}",
            LogContext(session_id=session_id, event=record.get("event"), fields={**record, "detail_path": str(self.session_file(session_id, "actuators.jsonl"))}),
        )

    def write_playback_snapshot(self, record: dict[str, Any]) -> None:
        """写入播放仲裁调试快照。

        主要逻辑：把当前 active、queue 和最近决策写入固定文件，便于调试接口和回放对比读取。
        参数：`record` 为播放仲裁快照。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        path = self.runs_root / "debug" / "playback.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def record_output_wav(
        self,
        *,
        session_id: str,
        stream_id: str,
        pcm: bytes,
        sample_rate: int,
        channels: int,
    ) -> None:
        """记录服务端下发的 PCM 输出音频。

        主要逻辑：把 actuator.speaker 的 pcm16le 载荷封装为 wav，作为回放和人工听检入口。
        参数：`session_id` 为会话，`stream_id` 为输出流，`pcm` 为原始音频字节。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        path = self.media_dir(session_id, "actuator.speaker") / f"output-{stream_id}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(channels)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm)
        log_info(
            self.logger,
            "输出音频已保存",
            LogContext(session_id=session_id, stream_id=stream_id, event="output.wav.saved", fields={"path": str(path), "bytes": len(pcm), "sample_rate": sample_rate, "detail_path": str(path)}),
        )

    def record_message(self, user_id: str, record: dict[str, Any]) -> None:
        session_id = str(record.get("session_id") or record.get("device_id") or "")
        if session_id:
            self.bind_device(user_id=user_id, device_id=session_id)
            path = self.session_dir(session_id) / "messages.jsonl"
        else:
            path = self.user_dir(user_id) / "messages.jsonl"
        self._append_jsonl(path, record)
        self.log_message(user_id, record, detail_path=path)

    def log_message(self, user_id: str, record: dict[str, Any], *, detail_path: Path | None = None) -> None:
        """只记录消息写入日志，不重复写文件。

        主要逻辑：ConversationMemoryService 接管消息文件写入后，仍复用 RunRecorder
        的终端日志格式。
        参数：`user_id` 为用户编号，`record` 为消息记录，`detail_path` 为实际文件路径。
        返回值：无。
        异常情况：无。
        """

        session_id = str(record.get("session_id") or record.get("device_id") or "")
        path = detail_path or (self.session_dir(session_id) / "messages.jsonl" if session_id else self.user_dir(user_id) / "messages.jsonl")
        log_info(
            self.logger,
            f"消息写入 {record.get('role')}",
            LogContext(
                user_id=user_id,
                session_id=record.get("session_id"),
                event=record.get("event"),
                fields={"role": record.get("role"), "text": _compact_text(record.get("content")), "detail_path": str(path)},
            ),
        )

    def load_messages(self, *, user_id: str, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """读取同一用户同一设备的历史消息。

        主要逻辑：优先读取 `runs/<user_id>/<session_id>/messages.jsonl`；如果会话尚未
        绑定用户，也会按传入 user_id 绑定目录。读取失败或 JSON 行损坏时跳过坏行，
        避免一条历史记录阻塞当前对话。
        参数：`user_id` 为用户编号，`session_id` 为设备级会话编号，`limit` 为最多返回条数。
        返回值：按原始写入顺序返回最近 `limit` 条消息。
        异常情况：文件不存在时返回空列表。
        """

        if not user_id or not session_id or limit <= 0:
            return []
        path = self.session_dir(session_id, user_id=user_id) / "messages.jsonl"
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
        return records[-limit:]

    def record_stream_payload(self, chunk: StreamChunk) -> None:
        name = "input" if chunk.stream_type.startswith("sensor.") else "output"
        self.bind_device(user_id=chunk.user_id, device_id=chunk.session_id)
        path = self.media_dir(chunk.session_id, chunk.stream_type) / f"{name}-{chunk.stream_id}.pcm"
        with path.open("ab") as handle:
            handle.write(chunk.payload)

    def record_provider_input_audio(self, chunk: StreamChunk) -> dict[str, Any] | None:
        """保存实际追加给 Omni provider 的麦克风音频。

        主要逻辑：仅处理 Omni 链路当前发送给 provider 的 `sensor.mic/pcm16le`
        载荷，同时落一份原始 PCM 和一份随写随可播放的 WAV。WAV 头会在每次追加后
        更新，方便真机测试中途直接打开听检。
        参数：`chunk` 为已经完成音频归一化、并成功 append 到 provider 的分片。
        返回值：保存路径和累计统计；非麦克风、非 pcm16le 或空载荷时返回 None。
        异常情况：文件系统不可写时抛出底层 IO 异常。
        """

        if chunk.stream_type != "sensor.mic" or chunk.codec != "pcm16le" or not chunk.payload:
            return None
        self.bind_device(user_id=chunk.user_id, device_id=chunk.session_id)
        directory = self.media_dir(chunk.session_id, chunk.stream_type)
        wav_path = directory / f"provider-input-{chunk.stream_id}.wav"
        pcm_path = directory / f"provider-input-{chunk.stream_id}.pcm"
        with pcm_path.open("ab") as handle:
            handle.write(chunk.payload)
        self._append_pcm16le_wav(
            wav_path,
            chunk.payload,
            sample_rate=chunk.sample_rate,
            channels=chunk.channels,
        )
        key = (chunk.session_id, chunk.stream_id)
        stats = self._provider_input_audio_stats.setdefault(
            key,
            {
                "chunk_count": 0,
                "audio_bytes": 0,
                "first_seq": chunk.seq,
                "last_seq": chunk.seq,
                "sample_rate": chunk.sample_rate,
                "channels": chunk.channels,
                "chunk_ms": chunk.duration_ms,
                "wav_path": str(wav_path),
                "pcm_path": str(pcm_path),
            },
        )
        stats["chunk_count"] = int(stats.get("chunk_count") or 0) + 1
        stats["audio_bytes"] = int(stats.get("audio_bytes") or 0) + len(chunk.payload)
        stats["last_seq"] = chunk.seq
        stats["sample_rate"] = chunk.sample_rate
        stats["channels"] = chunk.channels
        stats["chunk_ms"] = chunk.duration_ms
        return dict(stats)

    @staticmethod
    def _append_pcm16le_wav(path: Path, pcm: bytes, *, sample_rate: int, channels: int) -> None:
        """向 PCM16LE WAV 文件追加帧并刷新 RIFF 长度。

        主要逻辑：标准库 `wave` 更适合一次性写完文件；这里需要测试过程中持续
        追加并保持文件可播放，因此直接维护 44 字节 WAV 头。
        参数：`path` 为目标 WAV，`pcm` 为 16-bit little-endian 音频，`sample_rate`
        和 `channels` 描述音频格式。
        返回值：无。
        异常情况：采样率或声道数非法、文件不可写时抛出异常。
        """

        if sample_rate <= 0 or channels <= 0:
            raise ValueError("sample_rate and channels must be positive")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(RunRecorder._pcm16le_wav_header(sample_rate=sample_rate, channels=channels, data_size=0))
        with path.open("r+b") as handle:
            handle.seek(0, 2)
            handle.write(pcm)
            data_size = handle.tell() - 44
            handle.seek(4)
            handle.write(struct.pack("<I", 36 + data_size))
            handle.seek(40)
            handle.write(struct.pack("<I", data_size))

    @staticmethod
    def _pcm16le_wav_header(*, sample_rate: int, channels: int, data_size: int) -> bytes:
        byte_rate = sample_rate * channels * 2
        block_align = channels * 2
        return b"".join(
            [
                b"RIFF",
                struct.pack("<I", 36 + data_size),
                b"WAVE",
                b"fmt ",
                struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, 16),
                b"data",
                struct.pack("<I", data_size),
            ]
        )

    def media_dir(self, session_id: str, stream_type: str) -> Path:
        """返回某类媒体或传感器数据的子目录。

        主要逻辑：音频、照片、IMU、深度数据分别进入独立子目录，避免平铺在设备
        运行目录下。
        参数：`session_id` 即 device_id；`stream_type` 为 sensor/actuator 类型。
        返回值：已创建的子目录。
        异常情况：文件系统不可写时抛出底层 IO 异常。
        """

        dirname = _media_subdir_for_stream_type(stream_type)
        path = self.session_dir(session_id) / dirname
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _pop_stream_chunk_summary(self, *, session_id: str, stream_id: str) -> dict[str, Any]:
        """Return accumulated chunk stats for stream lifecycle logs."""

        result: dict[str, Any] = {}
        for event_name, prefix in (("stream.chunk.received", "input"), ("stream.chunk.sent", "output")):
            stats = self._stream_chunk_stats.pop((session_id, stream_id, event_name), None)
            if not stats:
                continue
            result[f"{prefix}_chunk_count"] = stats.get("count")
            result[f"{prefix}_bytes"] = stats.get("bytes")
            result[f"{prefix}_first_seq"] = stats.get("first_seq")
            result[f"{prefix}_last_seq"] = stats.get("last_seq")
            result[f"{prefix}_duration_ms"] = _elapsed_ms(float(stats.get("started_at") or 0), float(stats.get("last_at") or 0))
        return result

    def _record_delta_summary_start(
        self,
        *,
        session_id: str,
        event: str,
        record: dict[str, Any],
        context: LogContext,
    ) -> None:
        """记录高频 delta 的首包日志，并累积摘要数据。

        主要逻辑：终端只输出首个 delta 到达时机，后续 delta 只更新计数、字节数
        和文本预览；完整事件仍写入 jsonl。
        参数：`session_id/event/record/context` 描述当前 delta。
        返回值：无。
        异常情况：无。
        """

        key = (session_id, event, str(record.get("stream_id") or ""))
        now = time.monotonic()
        stats = self._delta_stats.setdefault(
            key,
            {
                "count": 0,
                "bytes": 0,
                "text": "",
                "started_at": now,
                "last_at": now,
                "first_seq": record.get("seq"),
                "stream_id": record.get("stream_id"),
            },
        )
        stats["count"] = int(stats.get("count") or 0) + 1
        stats["last_at"] = now
        stats["bytes"] = int(stats.get("bytes") or 0) + int(
            record.get("audio_bytes") or record.get("payload_size") or record.get("delta_base64_len") or 0
        )
        text = record.get("text") or record.get("delta") or record.get("transcript")
        if text:
            stats["text"] = _compact_text(str(stats.get("text") or "") + str(text), limit=160)
        if stats["count"] != 1:
            return
        request_started_at = self._model_request_started_at.get(session_id)
        fields = {
            **context.fields,
            "stream_id": record.get("stream_id"),
            "first_delta_after_ms": _elapsed_ms(request_started_at, now),
            "detail_path": str(self.session_file(session_id, "agent-events.jsonl")),
        }
        log_info(self.logger, f"首个 delta {event}", LogContext(user_id=context.user_id, session_id=session_id, event=event, fields=fields))

    def _record_delta_summary_done(
        self,
        *,
        session_id: str,
        done_event: str,
        record: dict[str, Any],
        context: LogContext,
    ) -> None:
        """记录高频 delta 的完成摘要。

        主要逻辑：在 done 事件到达时输出总片数、总字节数、首尾间隔和文本预览。
        参数：`done_event` 为完成事件，`record` 为原始事件。
        返回值：无。
        异常情况：无。
        """

        delta_event = DELTA_SUMMARY_DONE_EVENTS[done_event]
        key = (session_id, delta_event, str(record.get("stream_id") or ""))
        stats = self._delta_stats.pop(key, None)
        if stats is None:
            log_info(self.logger, f"delta 完成 {done_event}", context)
            return
        now = time.monotonic()
        text = record.get("text") or record.get("transcript") or stats.get("text")
        visible_text = None if done_event in DELTA_DONE_TEXT_DUPLICATED_BY_MESSAGE_EVENTS else _compact_text(text, limit=160)
        log_info(
            self.logger,
            f"delta 完成 {done_event}",
            LogContext(
                user_id=context.user_id,
                session_id=session_id,
                event=done_event,
                fields={
                    **context.fields,
                    "delta_event": delta_event,
                    "delta_count": stats.get("count"),
                    "bytes": stats.get("bytes"),
                    "duration_ms": _elapsed_ms(float(stats.get("started_at") or now), now),
                    "text": visible_text,
                    "detail_path": str(self.session_file(session_id, "agent-events.jsonl")),
                },
            ),
        )

    @staticmethod
    def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def _bind_from_record(self, session_id: str, record: dict[str, Any]) -> None:
        user_id = record.get("user_id")
        if user_id:
            self.bind_device(user_id=str(user_id), device_id=session_id)

    def _device_dir(self, user_id: str, device_id: str) -> Path:
        path = self.user_dir(user_id) / self._safe_path_part(device_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _safe_path_part(value: str) -> str:
        text = str(value)
        return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in text) or "_"


def _media_subdir_for_stream_type(stream_type: str) -> str:
    if stream_type in {"sensor.mic", "actuator.speaker"} or stream_type.startswith("audio."):
        return "audio"
    if stream_type == "sensor.rgb":
        return "photos"
    if stream_type == "sensor.imu":
        return "imu"
    if stream_type in {"sensor.depth", "sensor.tof"}:
        return "depth"
    return "assets"


def _compact_text(value: Any, limit: int = 120) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= limit else f"{text[:limit]}..."


def _is_high_frequency_control_progress(event: Event) -> bool:
    """判断是否是逐帧类高频控制进度。"""

    if event.event_name != "command.progress":
        return False
    payload = event.payload or {}
    status = str(payload.get("status") or "").strip()
    if status in HIGH_FREQUENCY_CONTROL_PROGRESS_STATUSES:
        return True
    return str(payload.get("message") or "").strip().lower() == "peer video frame processed"


def _control_event_error_message(event: Event, payload: dict[str, Any], error_fields: dict[str, str | None]) -> str | None:
    """只在真正错误事件中输出 error_message。"""

    has_provider_error = bool(payload.get("provider_error_message") or payload.get("provider_error_code") or payload.get("provider_error_type"))
    if event.event_name.startswith("system.error") or event.event_name == "command.failed" or payload.get("error_type") or has_provider_error:
        return error_fields.get("error_message") or _compact_text(payload.get("message"), limit=1000)
    return None


def _is_quiet_task_signal(record: dict[str, Any]) -> bool:
    """判断任务信号是否只适合落盘、不适合刷终端。"""

    if record.get("signal_name") != "task.event.dispatch.accepted":
        return False
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    return payload.get("task_event_type") == "process" and not record.get("requires_agent_decision") and not record.get("allow_direct_notify")


def _is_quiet_agent_turn_state(record: dict[str, Any]) -> bool:
    """判断 Agent turn 状态事件是否属于热路径噪音。

    主要逻辑：Realtime 的音频片段状态、Text 的文本释放和音频 final 检查都可能
    在一次回复中反复出现；完整事件仍写入 runs，终端不再逐条打印。
    参数：`record` 为 Agent 事件记录。
    返回值：需要静默终端输出时返回 True。
    异常情况：无。
    """

    return record.get("event") == "agent.turn_state.changed" and record.get("reason") in QUIET_AGENT_TURN_STATE_REASONS


def _error_log_fields(value: Any) -> dict[str, str | None]:
    """从 provider 或 SDK 错误对象中提取终端可读字段。

    主要逻辑：provider 错误经常是嵌套 dict，或已经被 `str(dict)` 转成字符串。
    这里尽量解析出稳定的 event_id、code/type/message，避免终端只看到一段被截断的
    Python dict 字符串。
    参数：`value` 为错误对象、字符串或包含 payload/error 的记录。
    返回值：适合放入日志 extra 的字段。
    异常情况：解析失败时回退到原始文本摘要。
    """

    parsed = _parse_error_value(value)
    raw_message = _compact_text(value, limit=1000)
    if not isinstance(parsed, dict):
        return {"error_message": raw_message}
    error_obj = parsed.get("error") if isinstance(parsed.get("error"), dict) else {}
    payload = parsed.get("payload") if isinstance(parsed.get("payload"), dict) else {}
    if not error_obj and isinstance(payload.get("error"), dict):
        error_obj = payload.get("error") or {}
    provider_message = (
        error_obj.get("message")
        or parsed.get("message")
        or payload.get("message")
        or parsed.get("error_message")
        or raw_message
    )
    return {
        "error_message": _compact_text(provider_message, limit=1000),
        "provider_error_code": _compact_text(error_obj.get("code") or parsed.get("code") or payload.get("code"), limit=200),
        "provider_error_type": _compact_text(error_obj.get("type") or parsed.get("type") or payload.get("type"), limit=200),
        "provider_event_id": _compact_text(parsed.get("event_id") or payload.get("event_id"), limit=200),
    }


def _parse_error_value(value: Any) -> Any:
    """解析错误值，兼容 dict、JSON 字符串和 Python dict 字符串。"""

    if isinstance(value, dict):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text[0] in "{[":
        try:
            return json.loads(text)
        except Exception:
            pass
        try:
            return literal_eval(text)
        except Exception:
            return None
    return None


def _elapsed_ms(start: float | None, end: float | None) -> int | None:
    if start is None or end is None:
        return None
    return int((end - start) * 1000)


def _model_request_terminal_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    """构造终端首次模型请求快照。

    主要逻辑：终端日志只展示开发者判断提示词和输入质量所需的模型可见内容；
    `instructions`、`history_messages` 等 Realtime 中间字段不在终端重复打印，避免
    与 `messages[0].content` 混淆。完整原始记录仍写入 `model-request.json`。
    参数：`record` 为 RunRecorder 收到的模型请求。
    返回值：可打印的紧凑请求快照。
    异常情况：无。
    """

    snapshot: dict[str, Any] = {}
    for key in ("provider", "model", "runner", "user_id", "session_id"):
        if key in record:
            snapshot[key] = record[key]
    messages = record.get("messages")
    if isinstance(messages, list):
        snapshot["messages"] = messages
    elif record.get("prompt"):
        snapshot["messages"] = [{"role": "system", "content": record.get("prompt")}]
    elif record.get("instructions"):
        snapshot["messages"] = [{"role": "system", "content": record.get("instructions")}]
    if "tools" in record:
        snapshot["tools"] = record.get("tools") or []
    if "tool_count" in record:
        snapshot["tool_count"] = record.get("tool_count")
    elif "tools" in snapshot:
        snapshot["tool_count"] = len(snapshot.get("tools") or [])
    if "prompts" in record:
        snapshot["prompts"] = record.get("prompts") or []
    if "context_sources" in record:
        snapshot["context_sources"] = record.get("context_sources") or []
    if "warnings" in record:
        snapshot["warnings"] = record.get("warnings") or []
    return snapshot


class TurnRecorder:
    """单轮交互记录器。

    主要功能：吸收 RunRecorder 的写入能力，为回放提供输入流、转写、模型请求、
    Tool trace、TaskSignal、输出流和 result 的稳定入口。
    """

    def __init__(self, runs_root: str | Path = "runs/default-app") -> None:
        self.recorder = RunRecorder(runs_root)

    def record_input_stream(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_stream_event(session_id, {"direction": "input", **record})

    def record_transcript(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_agent_event(session_id, {"event": "transcript", **record})

    def record_model_request(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_model_request(session_id, record)

    def record_agent_event(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_agent_event(session_id, record)

    def record_tool_trace(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_tool_trace(session_id, record)

    def record_task_signal(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_task_signal(session_id, record)

    def record_output_stream(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_stream_event(session_id, {"direction": "output", **record})

    def write_result(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.write_result(session_id, record)
