from __future__ import annotations

from dataclasses import dataclass, field

from task.base.task_context import TaskContext


@dataclass(slots=True)
class TaskContextStore:
    _contexts: dict[str, TaskContext] = field(default_factory=dict)

    def put(self, context: TaskContext) -> None:
        self._contexts[context.task_id] = context

    def get(self, task_id: str) -> TaskContext | None:
        return self._contexts.get(task_id)
