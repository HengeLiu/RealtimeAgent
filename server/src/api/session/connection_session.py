from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol


class Transport(Protocol):
    def send(self, payload: str) -> None: ...


@dataclass(slots=True)
class ConnectionSession:
    connection_id: str
    transport: Transport
    device_id: str | None = None
    module: str | None = None
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime | None = None

    def bind_device(self, *, device_id: str, module: str | None = None) -> None:
        self.device_id = device_id
        self.module = module
        self.last_heartbeat_at = datetime.now(timezone.utc)

    def mark_heartbeat(self) -> None:
        self.last_heartbeat_at = datetime.now(timezone.utc)

    def mark_closed(self) -> None:
        self.closed_at = datetime.now(timezone.utc)

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None

    def is_stale(self, timeout_seconds: int) -> bool:
        if self.is_closed:
            return True
        return datetime.now(timezone.utc) - self.last_heartbeat_at > timedelta(seconds=timeout_seconds)
