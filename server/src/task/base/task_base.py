from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from task.base.task_context import TaskContext


@dataclass(slots=True)
class TaskResult:
    summary: str
    data: dict[str, Any]


class TaskBase(ABC):
    task_type: str

    def __init__(self, context: TaskContext) -> None:
        self.context = context

    @abstractmethod
    def validate_input(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def prepare(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def run(self) -> TaskResult:
        raise NotImplementedError

    def pause(self) -> None:
        return None

    def resume(self) -> None:
        return None

    def cancel(self) -> None:
        return None

    def build_result(self) -> dict[str, Any]:
        return self.context.result_context
