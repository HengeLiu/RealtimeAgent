from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from protocol.enums import Priority


_PRIORITY_WEIGHT = {
    Priority.CRITICAL: 0,
    Priority.HIGH: 1,
    Priority.NORMAL: 2,
    Priority.LOW: 3,
}


@dataclass(slots=True)
class TaskScheduler:
    _queue: list[tuple[int, int, str]] = field(default_factory=list)
    _seq: int = 0

    def enqueue(self, task_id: str, priority: Priority) -> None:
        self._seq += 1
        heapq.heappush(self._queue, (_PRIORITY_WEIGHT[priority], self._seq, task_id))

    def dequeue(self) -> str | None:
        if not self._queue:
            return None
        return heapq.heappop(self._queue)[2]

    def __len__(self) -> int:
        return len(self._queue)
