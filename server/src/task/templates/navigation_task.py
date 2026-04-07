from __future__ import annotations

from task.base import TaskBase, TaskResult


class NavigationTask(TaskBase):
    task_type = "navigation"

    def validate_input(self) -> None:
        destination = self.context.input_context.get("destination")
        if not destination:
            raise ValueError("destination is required")

    def prepare(self) -> None:
        self.context.runtime_context["route_status"] = "planning"

    def run(self) -> TaskResult:
        destination = self.context.input_context["destination"]
        self.context.result_context = {"destination": destination, "status": "planned"}
        return TaskResult(summary="navigation_planned", data=self.context.result_context)
