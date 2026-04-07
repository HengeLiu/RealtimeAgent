from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from protocol.enums import MessageType, Priority


@dataclass(slots=True)
class Endpoint:
    device_id: str
    module: str

    def to_dict(self) -> dict[str, str]:
        return {"device_id": self.device_id, "module": self.module}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Endpoint":
        return cls(device_id=raw["device_id"], module=raw["module"])


@dataclass(slots=True)
class Envelope:
    message_id: str
    trace_id: str
    message_type: MessageType
    message_name: str
    protocol_version: str
    source: Endpoint
    target: Endpoint
    timestamp: str
    payload: dict[str, Any]
    correlation_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    priority: Priority = Priority.NORMAL
    requires_ack: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.message_id:
            raise ValueError("message_id is required")
        if not self.trace_id:
            raise ValueError("trace_id is required")
        if "." not in self.message_name:
            raise ValueError("message_name must follow <domain>.<action>")
        if not self.source.device_id or not self.target.device_id:
            raise ValueError("source/target device_id is required")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "message_id": self.message_id,
            "trace_id": self.trace_id,
            "message_type": self.message_type.value,
            "message_name": self.message_name,
            "protocol_version": self.protocol_version,
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "timestamp": self.timestamp,
            "payload": self.payload,
            "priority": self.priority.value,
            "requires_ack": self.requires_ack,
            "metadata": self.metadata,
        }
        if self.correlation_id:
            data["correlation_id"] = self.correlation_id
        if self.task_id:
            data["task_id"] = self.task_id
        if self.session_id:
            data["session_id"] = self.session_id
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Envelope":
        envelope = cls(
            message_id=raw["message_id"],
            trace_id=raw["trace_id"],
            message_type=MessageType(raw["message_type"]),
            message_name=raw["message_name"],
            protocol_version=raw["protocol_version"],
            source=Endpoint.from_dict(raw["source"]),
            target=Endpoint.from_dict(raw["target"]),
            timestamp=raw["timestamp"],
            payload=dict(raw.get("payload") or {}),
            correlation_id=raw.get("correlation_id"),
            task_id=raw.get("task_id"),
            session_id=raw.get("session_id"),
            priority=Priority(raw.get("priority", Priority.NORMAL.value)),
            requires_ack=bool(raw.get("requires_ack", False)),
            metadata=dict(raw.get("metadata") or {}),
        )
        envelope.validate()
        return envelope



def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
