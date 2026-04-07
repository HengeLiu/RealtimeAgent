from __future__ import annotations

from backend_task_core.manager.task_manager import TaskManager
from protocol.enums import Priority, SkillMode, TaskSource
from skill.base import SkillBase, SkillRequest, SkillResult


class TaskManageSkill(SkillBase):
    name = "task_manage_skill"
    description = "Create/query/cancel backend tasks via TaskManager."
    input_schema = {"type": "object"}
    output_schema = {"type": "object"}
    mode = SkillMode.TASK_SPAWN

    def __init__(self, task_manager: TaskManager) -> None:
        self._task_manager = task_manager

    def execute(self, request: SkillRequest) -> SkillResult:
        action = request.input.get("action", "create")
        if action == "create":
            task = self._task_manager.create_task(
                task_type=str(request.input["task_type"]),
                source=TaskSource.SKILL,
                priority=Priority(request.input.get("priority", Priority.NORMAL.value)),
                input_data=dict(request.input.get("task_input") or {}),
            )
            return SkillResult(
                status="accepted",
                data={"task_id": task.task_id, "status": task.status.value},
                summary="task_spawned",
                task_id=task.task_id,
            )

        task_id = str(request.input["task_id"])
        if action == "query":
            task = self._task_manager.get(task_id)
            if not task:
                return SkillResult(status="failed", summary="task_not_found", error={"task_id": task_id})
            return SkillResult(status="completed", data=task.to_dict(), summary="direct_result", task_id=task_id)

        if action == "cancel":
            task = self._task_manager.cancel(task_id)
            return SkillResult(
                status="completed",
                data={"task_id": task.task_id, "status": task.status.value},
                summary="direct_result",
                task_id=task.task_id,
            )

        return SkillResult(status="failed", summary="unsupported_action", error={"action": action})
