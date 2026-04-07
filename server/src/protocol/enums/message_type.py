from __future__ import annotations

from enum import Enum

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - py3.10 and lower fallback
    class StrEnum(str, Enum):
        pass


class MessageType(StrEnum):
    COMMAND = "command"
    EVENT = "event"
    STATE = "state"
    STREAM = "stream"
    ACK = "ack"
    ERROR = "error"
