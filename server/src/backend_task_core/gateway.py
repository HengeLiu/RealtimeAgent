"""backend-task-core 最小访问网关。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agent_core.context import generate_id
from agent_core.context.models import now_ms
from backend_task_core.models import TaskRuntime
from infra.errors import ErrorCode, build_error


class TaskGateway(ABC):
    """任务网关抽象接口。"""

    @abstractmethod
    def create_task(
        self,
        *,
        task_type: str,
        session_id: str,
        device_id: str,
        input_data: dict[str, Any],
    ) -> TaskRuntime:
        """创建任务实例。"""

    @abstractmethod
    def query_task(self, task_id: str) -> TaskRuntime:
        """查询任务实例。"""

    @abstractmethod
    def cancel_task(self, task_id: str) -> TaskRuntime:
        """取消任务实例。"""


class InMemoryTaskGateway(TaskGateway):
    """Phase E 用内存任务网关。"""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRuntime] = {}

    def create_task(
        self,
        *,
        task_type: str,
        session_id: str,
        device_id: str,
        input_data: dict[str, Any],
    ) -> TaskRuntime:
        task_id = generate_id("task")
        runtime = TaskRuntime(
            task_id=task_id,
            task_type=task_type,
            version="v1",
            session_id=session_id,
            device_id=device_id,
            state="scheduled",
            input=dict(input_data),
            context={
                "created_by": "agent_core_phase_e",
                "scheduled_at_ms": now_ms(),
            },
            started_at_ms=now_ms(),
        )
        self._tasks[task_id] = runtime
        return runtime

    def query_task(self, task_id: str) -> TaskRuntime:
        runtime = self._tasks.get(task_id)
        if runtime is None:
            raise build_error(
                ErrorCode.TASK_NOT_FOUND,
                "目标任务不存在",
                details={"task_id": task_id},
            )
        runtime.updated_at_ms = now_ms()
        return runtime

    def cancel_task(self, task_id: str) -> TaskRuntime:
        runtime = self.query_task(task_id)
        if runtime.state in {"failed", "timeout", "cancelled", "completed"}:
            return runtime
        runtime.state = "cancelled"
        runtime.completed_at_ms = now_ms()
        runtime.updated_at_ms = runtime.completed_at_ms
        runtime.result = {"message": "任务已取消"}
        return runtime
