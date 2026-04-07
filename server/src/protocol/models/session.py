from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from protocol.enums import SessionStatus, SessionType
from protocol.models.base import Serializable


@dataclass(slots=True)
class Session(Serializable):
    session_id: str
    session_type: SessionType
    participants: list[str]
    status: SessionStatus
    started_at: str
    ended_at: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Session":
        return cls(
            session_id=raw["session_id"],
            session_type=SessionType(raw["session_type"]),
            participants=list(raw.get("participants", [])),
            status=SessionStatus(raw["status"]),
            started_at=raw["started_at"],
            ended_at=raw.get("ended_at"),
            context=dict(raw.get("context") or {}),
        )
