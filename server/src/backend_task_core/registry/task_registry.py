from __future__ import annotations

from dataclasses import dataclass, field
from typing import Type

from task.base import TaskBase


@dataclass(slots=True)
class TaskRegistry:
    _task_types: dict[str, Type[TaskBase]] = field(default_factory=dict)

    def register(self, task_type: str, task_cls: Type[TaskBase]) -> None:
        if task_type in self._task_types:
            raise ValueError(f"Task type already registered: {task_type}")
        self._task_types[task_type] = task_cls

    def get(self, task_type: str) -> Type[TaskBase]:
        task_cls = self._task_types.get(task_type)
        if not task_cls:
            raise KeyError(f"Unknown task type: {task_type}")
        return task_cls

    def list_task_types(self) -> list[str]:
        return sorted(self._task_types.keys())
