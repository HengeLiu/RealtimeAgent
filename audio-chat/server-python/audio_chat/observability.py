from __future__ import annotations

import json
import logging
import sys
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audio_chat.protocol import Event, StreamChunk

NOISY_LIBRARY_LOG_LEVELS: tuple[tuple[str, int], ...] = (
    ("aiohttp.access", logging.INFO),
    ("dashscope", logging.WARNING),
    ("httpcore", logging.WARNING),
    ("httpx", logging.WARNING),
    ("openai", logging.WARNING),
)
STANDARD_LOG_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
HIGH_FREQUENCY_AGENT_EVENTS = {
    "input_transcript.delta",
    "assistant_text.delta",
    "omni.response.audio.delta.decoded",
    "omni.response.audio_transcript.delta",
    "assistant_audio.delta",
}
QUIET_AGENT_EVENTS = {
    "realtime.input_audio.appended",
    "omni.response.audio.delta",
    "omni.response.function_call_arguments.delta",
    "omni.response.tool_call_arguments.delta",
}
DELTA_SUMMARY_DONE_EVENTS = {
    "assistant_audio.done": "assistant_audio.delta",
    "omni.response.audio.done": "omni.response.audio.delta.decoded",
    "input_transcript.done": "input_transcript.delta",
    "omni.conversation.item.input_audio_transcription.completed": "input_transcript.delta",
    "omni.response.audio_transcript.done": "omni.response.audio_transcript.delta",
}
QUIET_CONTROL_EVENTS = {"control.device.heartbeat.received"}
DEBUG_ONLY_CONTROL_PREFIXES = ("stream.",)


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
            if value is not None and key not in STANDARD_LOG_RECORD_FIELDS:
                result[key] = value
        return result


class LineFormatter(logging.Formatter):
    """开发调试用单行日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        """格式化为 `time level logger message key=value`。"""

        timestamp = datetime.now(tz=timezone.utc).astimezone().isoformat(timespec="milliseconds")
        message = record.getMessage()
        line = f"{timestamp} {record.levelname} {record.name} {message}"
        standard = STANDARD_LOG_RECORD_FIELDS | {"exc_info", "exc_text", "stack_info"}
        fields: list[str] = []
        for key, value in sorted(record.__dict__.items()):
            if key in standard or key.startswith("_"):
                continue
            fields.append(f"{key}={_format_log_value(value)}")
        if fields:
            line = f"{line} {' '.join(fields)}"
        return line


def configure_console_logging(level: str = "INFO") -> None:
    """配置开发终端日志。

    主要逻辑：绑定 stdout 单行日志格式，并压低第三方库的噪声日志。
    参数：`level` 为 DEBUG/INFO/WARNING/ERROR 等标准级别名称。
    返回值：无。
    异常情况：未知级别时回退 INFO。
    """

    resolved = getattr(logging, str(level or "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(resolved)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(LineFormatter())
    root.addHandler(handler)
    for name, logger_level in NOISY_LIBRARY_LOG_LEVELS:
        logging.getLogger(name).setLevel(logger_level)


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
    def __init__(self, runs_root: str | Path = "runs/audio-chat") -> None:
        self.runs_root = Path(runs_root).expanduser().resolve()
        self.logger = get_logger("audio_chat.runs")
        self._stream_chunk_stats: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._agent_event_counts: dict[tuple[str, str], int] = {}
        self._model_request_started_at: dict[str, float] = {}
        self._delta_stats: dict[tuple[str, str], dict[str, Any]] = {}
        self._first_model_request_logged = False

    def session_dir(self, session_id: str) -> Path:
        path = self.runs_root / "sessions" / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def user_dir(self, user_id: str) -> Path:
        path = self.runs_root / "users" / user_id
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
            self._append_jsonl(self.session_dir(event.session_id) / "events.jsonl", event.to_dict())
        self._append_jsonl(self.runs_root / "control-events.jsonl", event.to_dict())
        if event.event_name in QUIET_CONTROL_EVENTS:
            return
        payload = event.payload or {}
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
                    "reason": payload.get("reason"),
                    "error_type": payload.get("error_type"),
                    "error_message": _compact_text(payload.get("message")),
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
            self._append_jsonl(self.session_dir(event.session_id) / "control-routes.jsonl", payload)
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
        self._append_jsonl(self.session_dir(session_id) / "agent-events.jsonl", record)
        self._append_jsonl(self.session_dir(session_id) / "model-events.jsonl", record)
        event = str(record.get("event") or "")
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
                "error_message": _compact_text(record.get("message") or record.get("error"), limit=500),
                "bytes": record.get("audio_bytes") or record.get("payload_size"),
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
        log_info(self.logger, f"Agent事件 {event}", context)

    def record_tool_trace(self, session_id: str, record: dict[str, Any]) -> None:
        """记录 Tool 调用轨迹。

        主要逻辑：写入稳定 `tool-events.jsonl`，并保留旧 `tool-trace.jsonl`
        兼容入口，供回放和排障读取。
        参数：`session_id` 为会话，`record` 为工具调用结构。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        self._append_jsonl(self.session_dir(session_id) / "tool-events.jsonl", record)
        self._append_jsonl(self.session_dir(session_id) / "tool-trace.jsonl", record)
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

    def record_task_event(self, session_id: str, record: dict[str, Any]) -> None:
        """记录 TaskEvent。

        主要逻辑：写入 `task-events.jsonl`。
        参数：`session_id` 为会话或任务标识，`record` 为任务事件结构。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        self._append_jsonl(self.session_dir(session_id) / "task-events.jsonl", record)
        log_info(
            self.logger,
            f"任务事件 {record.get('event')}",
            LogContext(session_id=session_id, event=record.get("event"), fields={**record, "detail_path": str(self.session_file(session_id, "task-events.jsonl"))}),
        )

    def record_asset_event(self, session_id: str, record: dict[str, Any]) -> None:
        """记录 Asset Service 事件。

        主要逻辑：写入 `assets.jsonl`，让回放产物能直接解释资产写入、request_id
        和 stream 类型。
        参数：`session_id` 为会话，`record` 为资产事件。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
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
        `model-request.json` 内容以漂亮 JSON 打到终端，方便开发者先看 system prompt、
        messages 和 tools；之后的请求只保留摘要日志和落盘文件，避免刷屏。
        参数：`session_id` 为当前会话，`record` 为模型请求快照。
        返回值：无。
        异常情况：JSON 序列化异常时退回 `str(record)`。
        """

        try:
            snapshot = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception:
            snapshot = str(record)
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
        path = self.session_dir(session_id) / "result.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def record_playback_result(self, session_id: str, record: dict[str, Any]) -> None:
        path = self.session_dir(session_id) / "playback-result.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def record_system_event(self, record: dict[str, Any]) -> None:
        self._append_jsonl(self.runs_root / "system-events.jsonl", record)
        event_name = record.get("event") or record.get("event_name")
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        log_method = log_warning if payload.get("severity") == "warning" or record.get("severity") == "warning" else log_error
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
                    "error_message": _compact_text(record.get("message") or payload.get("message")),
                    "call_id": record.get("call_id") or payload.get("call_id"),
                    "suppressed_count": payload.get("suppressed_count"),
                    "stream_type": record.get("stream_type"),
                    "detail_path": str(self.runs_root / "system-events.jsonl"),
                    "session_detail_path": str(self.session_file(str(record.get("session_id")), "events.jsonl")) if record.get("session_id") else None,
                },
            ),
        )

    def record_playback_decision(self, session_id: str, record: dict[str, Any]) -> None:
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
        path = self.session_dir(session_id) / f"output-{stream_id}.wav"
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
        self._append_jsonl(self.user_dir(user_id) / "messages.jsonl", record)
        log_info(
            self.logger,
            f"消息写入 {record.get('role')}",
            LogContext(
                user_id=user_id,
                session_id=record.get("session_id"),
                event=record.get("event"),
                fields={"role": record.get("role"), "text": _compact_text(record.get("content")), "detail_path": str(self.user_dir(user_id) / "messages.jsonl")},
            ),
        )

    def record_stream_payload(self, chunk: StreamChunk) -> None:
        name = "input" if chunk.stream_type.startswith("sensor.") else "output"
        path = self.session_dir(chunk.session_id) / f"{name}-{chunk.stream_id}.pcm"
        with path.open("ab") as handle:
            handle.write(chunk.payload)

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

        key = (session_id, event)
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
        key = (session_id, delta_event)
        stats = self._delta_stats.pop(key, None)
        if stats is None:
            log_info(self.logger, f"delta 完成 {done_event}", context)
            return
        now = time.monotonic()
        text = record.get("text") or record.get("transcript") or stats.get("text")
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
                    "text": _compact_text(text, limit=160),
                    "detail_path": str(self.session_file(session_id, "agent-events.jsonl")),
                },
            ),
        )

    @staticmethod
    def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _compact_text(value: Any, limit: int = 120) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= limit else f"{text[:limit]}..."


def _elapsed_ms(start: float | None, end: float) -> int | None:
    if start is None:
        return None
    return int((end - start) * 1000)


class TurnRecorder:
    """单轮交互记录器。

    主要功能：吸收 RunRecorder 的写入能力，为回放提供输入流、转写、模型请求、
    Tool trace、TaskEvent、输出流和 result 的稳定入口。
    """

    def __init__(self, runs_root: str | Path = "runs/audio-chat") -> None:
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

    def record_task_event(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_task_event(session_id, record)

    def record_output_stream(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_stream_event(session_id, {"direction": "output", **record})

    def write_result(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.write_result(session_id, record)
