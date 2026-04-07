from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

Subscriber = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class TaskEventBus:
    _subscribers: dict[str, list[Subscriber]] = field(default_factory=dict)

    def subscribe(self, event_name: str, fn: Subscriber) -> None:
        self._subscribers.setdefault(event_name, []).append(fn)

    def publish(self, event_name: str, payload: dict[str, Any]) -> None:
        for fn in self._subscribers.get(event_name, []):
            fn(payload)
