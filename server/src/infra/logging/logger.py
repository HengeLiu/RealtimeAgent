"""结构化日志模块。"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

DATA_URL_PATTERN = re.compile(
    r"data:(?P<mime>(?:audio|image)/[A-Za-z0-9.+-]+);base64,(?P<body>.*?)(?=(?:['\"}\],)]|$))",
    re.DOTALL,
)


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

    def to_dict(self) -> dict[str, str]:
        """转换为不含空值的字典。

        返回值：
        1. 字段名到字段值的字典。
        """

        result: dict[str, str] = {}
        if self.trace_id:
            result["trace_id"] = self.trace_id
        if self.session_id:
            result["session_id"] = self.session_id
        if self.device_id:
            result["device_id"] = self.device_id
        if self.message_id:
            result["message_id"] = self.message_id
        return result


class JsonFormatter(logging.Formatter):
    """JSON 日志格式化器。

    主要功能：
    1. 将日志输出转换为单行 JSON，便于采集与检索。
    2. 自动注入时间、级别、模块名和链路字段。
    """

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录。

        参数：
        1. `record`：标准日志记录对象。

        返回值：
        1. JSON 字符串。
        """

        payload: dict[str, Any] = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": sanitize_log_message(record.getMessage()),
        }
        for key in ("trace_id", "session_id", "device_id", "message_id"):
            value = getattr(record, key, None)
            if value:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


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


def configure_root_logger(level: str = "INFO") -> None:
    """配置全局日志器。

    主要逻辑：
    1. 清理默认处理器，避免重复打印。
    2. 绑定 JSON 输出处理器到标准输出。

    参数：
    1. `level`：日志级别。
    """

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


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
