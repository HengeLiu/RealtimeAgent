from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TaskContext:
    task_id: str
    task_type: str
    input_context: dict[str, Any] = field(default_factory=dict)
    runtime_context: dict[str, Any] = field(default_factory=dict)
    device_context: dict[str, Any] = field(default_factory=dict)
    result_context: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "input_context": self.input_context,
            "runtime_context": self.runtime_context,
            "device_context": self.device_context,
            "result_context": self.result_context,
        }
