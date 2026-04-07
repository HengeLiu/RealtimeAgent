from __future__ import annotations

from dataclasses import dataclass, field

from backend_task_core.event_bus.task_event_bus import TaskEventBus
from backend_task_core.manager.task_context_store import TaskContextStore
from backend_task_core.registry.task_registry import TaskRegistry
from backend_task_core.scheduler.task_scheduler import TaskScheduler
from backend_task_core.state_machine.task_state_machine import TaskStateMachine
from infra.clock.system_clock import SystemClock
from infra.idgen.id_generator import IdGenerator
from protocol.enums import Priority, TaskSource, TaskStatus
from protocol.models.task import TaskModel
from task.base.task_context import TaskContext


@dataclass(slots=True)
class TaskManager:
    task_registry: TaskRegistry
    state_machine: TaskStateMachine
    scheduler: TaskScheduler
    event_bus: TaskEventBus
    context_store: TaskContextStore
    _tasks: dict[str, TaskModel] = field(default_factory=dict)

    def create_task(
        self,
        *,
        task_type: str,
        source: TaskSource,
        priority: Priority = Priority.NORMAL,
        input_data: dict[str, object] | None = None,
    ) -> TaskModel:
        now = SystemClock.now_iso()
        task = TaskModel(
            task_id=IdGenerator.task_id(),
            task_type=task_type,
            source=source,
            status=TaskStatus.CREATED,
            priority=priority,
            created_at=now,
            updated_at=now,
            input=dict(input_data or {}),
        )
        context = TaskContext(task_id=task.task_id, task_type=task_type, input_context=task.input)
        self.context_store.put(context)
        self._tasks[task.task_id] = task

        self._transition(task, TaskStatus.QUEUED, "task.created")
        self.scheduler.enqueue(task.task_id, priority)
        return task

    def start_next(self) -> TaskModel | None:
        task_id = self.scheduler.dequeue()
        if not task_id:
            return None
        task = self._tasks[task_id]
        context = self.context_store.get(task_id)
        if not context:
            raise ValueError(f"task context missing: {task_id}")

        task_cls = self.task_registry.get(task.task_type)
        task_instance = task_cls(context)

        self._transition(task, TaskStatus.PREPARING, "task.preparing")
        task_instance.validate_input()
        task_instance.prepare()

        self._transition(task, TaskStatus.RUNNING, "task.started")
        result = task_instance.run()
        task.result = result.data
        self._transition(task, TaskStatus.COMPLETED, "task.completed", summary=result.summary)
        return task

    def cancel(self, task_id: str) -> TaskModel:
        task = self._tasks[task_id]
        self._transition(task, TaskStatus.CANCELLED, "task.cancelled")
        return task

    def get(self, task_id: str) -> TaskModel | None:
        return self._tasks.get(task_id)

    def list(self) -> list[TaskModel]:
        return list(self._tasks.values())

    def _transition(self, task: TaskModel, target: TaskStatus, event_name: str, **extras: object) -> None:
        task.status = self.state_machine.transition(task.status, target)
        task.updated_at = SystemClock.now_iso()
        if target == TaskStatus.RUNNING:
            task.started_at = task.updated_at
        if target in {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.TIMED_OUT}:
            task.ended_at = task.updated_at
        self.event_bus.publish(
            event_name,
            {
                "task_id": task.task_id,
                "task_type": task.task_type,
                "status": task.status.value,
                **extras,
            },
        )
