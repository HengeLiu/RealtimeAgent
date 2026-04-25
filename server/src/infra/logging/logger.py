"""结构化日志模块。"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

DATA_URL_PATTERN = re.compile(
    r"data:(?P<mime>(?:audio|image)/[A-Za-z0-9.+-]+);base64,(?P<body>.*?)(?=(?:['\"}\],)]|$))",
    re.DOTALL,
)
NOISY_LIBRARY_LOG_LEVELS: tuple[tuple[str, int], ...] = (
    ("dashscope", logging.WARNING),
    ("httpcore", logging.WARNING),
    ("httpx", logging.WARNING),
    ("openai.agents", logging.WARNING),
)
STANDARD_LOG_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


@dataclass(slots=True)
class LogContext:
    """日志上下文对象。

    主要功能：
    1. 统一传递链路追踪字段。
    2. 避免日志调用处重复拼接同类字段。

    主要属性：
    1. `trace_id`：链路追踪编号。
    2. `session_id`：会话编号。
    3. `device_id`：设备编号。
    4. `message_id`：消息编号。
    """

    trace_id: str | None = None
    session_id: str | None = None
    device_id: str | None = None
    message_id: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为不含空值的字典。

        返回值：
        1. 字段名到字段值的字典。
        """

        result: dict[str, Any] = {}
        if self.trace_id:
            result["trace_id"] = self.trace_id
        if self.session_id:
            result["session_id"] = self.session_id
        if self.device_id:
            result["device_id"] = self.device_id
        if self.message_id:
            result["message_id"] = self.message_id
        for key, value in self.fields.items():
            if key in STANDARD_LOG_RECORD_FIELDS:
                continue
            if value is not None:
                result[key] = _json_safe(value)
        return result


class LineFormatter(logging.Formatter):
    """单行文本日志格式化器。

    主要功能：
    1. 按 `{timestamp}-{level}-{logger}-{message_id}-{message}` 格式输出。
    2. 将链路字段和业务字段追加为 `key=value`，便于联调时肉眼查看和文本检索。
    """

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录。

        参数：
        1. `record`：标准日志记录对象。

        返回值：
        1. 单行日志文本。
        """

        timestamp = datetime.now(tz=timezone.utc).isoformat()
        message = sanitize_log_message(record.getMessage())
        message_id = str(getattr(record, "message_id", "") or "-")
        parts = [
            timestamp,
            record.levelname,
            record.name,
            message_id,
            message,
        ]
        context_fields: dict[str, Any] = {}
        for key in ("trace_id", "session_id", "device_id", "message_id"):
            value = getattr(record, key, None)
            if value:
                context_fields[key] = value
        for key, value in sorted(record.__dict__.items()):
            if key in STANDARD_LOG_RECORD_FIELDS or key in context_fields:
                continue
            if key.startswith("_"):
                continue
            context_fields[key] = _json_safe(value)
        suffix = " ".join(
            f"{key}={_format_field_value(value)}"
            for key, value in context_fields.items()
            if key != "message_id"
        )
        line = "-".join(parts)
        if suffix:
            return f"{line} {suffix}"
        return line


# 兼容旧测试或旧导入名；实际输出已经不是 JSON。
JsonFormatter = LineFormatter


def _json_safe(value: Any) -> Any:
    """把日志上下文字段转换成 JSON 可序列化的数据。"""

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _format_field_value(value: Any) -> str:
    """格式化日志字段值。"""

    safe_value = _json_safe(value)
    if isinstance(safe_value, (dict, list)):
        return json.dumps(safe_value, ensure_ascii=False, separators=(",", ":"))
    return str(safe_value)


def sanitize_log_message(message: str) -> str:
    """对日志消息做轻量脱敏。

    主要逻辑：
    1. 脱敏音频与图片 `data:` URL 中的 base64 内容。
    2. 保留 MIME 类型，方便判断是音频还是图片载荷。

    参数：
    1. `message`：原始日志文本。

    返回值：
    1. 脱敏后的日志文本。
    """

    def _replace(match: re.Match[str]) -> str:
        mime = match.group("mime")
        return f"data:{mime};base64,<redacted>"

    sanitized = DATA_URL_PATTERN.sub(_replace, message)
    sanitized = sanitized.replace("<redacted>\\n", "<redacted>")
    sanitized = sanitized.replace("<redacted>\n", "<redacted>")
    return sanitized


def configure_root_logger(level: str = "INFO", log_file: str | None = None) -> None:
    """配置全局日志器。

    主要逻辑：
    1. 清理默认处理器，避免重复打印。
    2. 绑定单行文本输出处理器到标准输出。
    3. 若传入日志文件路径，则额外写入同格式日志文件。

    参数：
    1. `level`：日志级别。
    2. `log_file`：日志文件路径，留空时只输出到标准输出。
    """

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    formatter = LineFormatter()

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if log_file and log_file.strip():
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    configure_library_loggers()


def configure_library_loggers() -> None:
    """收敛第三方库日志级别。

    主要逻辑：
    1. 压低高频第三方调试日志，避免淹没业务主链路。
    2. 保留 WARNING 及以上级别，确保异常仍然可见。
    """

    for logger_name, level in NOISY_LIBRARY_LOG_LEVELS:
        logging.getLogger(logger_name).setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """获取命名日志器。

    参数：
    1. `name`：日志器名称。

    返回值：
    1. `logging.Logger` 对象。
    """

    return logging.getLogger(name)


def log_info(logger: logging.Logger, message: str, context: LogContext | None = None) -> None:
    """输出带上下文的 INFO 日志。

    主要逻辑：
    1. 从 `LogContext` 提取四个链路字段。
    2. 通过 `extra` 传入日志系统，统一由格式化器输出。

    参数：
    1. `logger`：日志器对象。
    2. `message`：日志内容。
    3. `context`：可选链路上下文。
    """

    extra = context.to_dict() if context else {}
    logger.info(message, extra=extra)


def log_warning(logger: logging.Logger, message: str, context: LogContext | None = None) -> None:
    """输出带上下文的 WARNING 日志。"""

    extra = context.to_dict() if context else {}
    logger.warning(message, extra=extra)


def log_error(logger: logging.Logger, message: str, context: LogContext | None = None) -> None:
    """输出带上下文的 ERROR 日志。"""

    extra = context.to_dict() if context else {}
    logger.error(message, extra=extra)


def log_debug(logger: logging.Logger, message: str, context: LogContext | None = None) -> None:
    """输出带上下文的 DEBUG 日志。"""

    extra = context.to_dict() if context else {}
    logger.debug(message, extra=extra)
