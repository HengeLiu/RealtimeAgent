from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "event_name": getattr(record, "event_name", record.getMessage()),
            "message": record.getMessage(),
        }
        for field in ("trace_id", "message_id", "task_id", "device_id"):
            value = getattr(record, field, None)
            if value:
                payload[field] = value
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)



def create_logger(name: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger



def log_event(
    logger: logging.Logger,
    level: int,
    event_name: str,
    *,
    trace_id: str | None = None,
    message_id: str | None = None,
    task_id: str | None = None,
    device_id: str | None = None,
    **kwargs: Any,
) -> None:
    logger.log(
        level,
        event_name,
        extra={
            "event_name": event_name,
            "trace_id": trace_id,
            "message_id": message_id,
            "task_id": task_id,
            "device_id": device_id,
            "extra_fields": kwargs,
        },
    )
