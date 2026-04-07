from __future__ import annotations

from dataclasses import dataclass

from backend_task_core.manager.task_manager import TaskManager
from protocol.enums import Priority, TaskSource
from skill.base import SkillRequest, SkillResult
from skill.registry import SkillRegistry


@dataclass(slots=True)
class SkillGateway:
    skill_registry: SkillRegistry

    def call(self, *, skill_name: str, trace_id: str, caller: str, input_data: dict[str, object]) -> SkillResult:
        return self.skill_registry.execute(
            skill_name,
            SkillRequest(trace_id=trace_id, caller=caller, input=dict(input_data)),
        )


@dataclass(slots=True)
class TaskGateway:
    task_manager: TaskManager

    def create(
        self,
        *,
        task_type: str,
        source: TaskSource,
        input_data: dict[str, object],
        priority: Priority = Priority.NORMAL,
    ):
        return self.task_manager.create_task(
            task_type=task_type,
            source=source,
            input_data=input_data,
            priority=priority,
        )

    def query(self, task_id: str):
        return self.task_manager.get(task_id)

    def cancel(self, task_id: str):
        return self.task_manager.cancel(task_id)
