from __future__ import annotations

from dataclasses import dataclass, field

from protocol.enums import TaskStatus


@dataclass(slots=True)
class TaskStateMachine:
    _transitions: dict[TaskStatus, set[TaskStatus]] = field(
        default_factory=lambda: {
            TaskStatus.CREATED: {TaskStatus.QUEUED},
            TaskStatus.QUEUED: {TaskStatus.PREPARING},
            TaskStatus.PREPARING: {TaskStatus.RUNNING},
            TaskStatus.RUNNING: {
                TaskStatus.WAITING_INPUT,
                TaskStatus.PAUSED,
                TaskStatus.COMPLETED,
                TaskStatus.CANCELLED,
                TaskStatus.FAILED,
                TaskStatus.TIMED_OUT,
            },
            TaskStatus.WAITING_INPUT: {TaskStatus.RUNNING},
            TaskStatus.PAUSED: {TaskStatus.RUNNING},
            TaskStatus.COMPLETED: set(),
            TaskStatus.CANCELLED: set(),
            TaskStatus.FAILED: set(),
            TaskStatus.TIMED_OUT: set(),
        }
    )

    def can_transition(self, current: TaskStatus, target: TaskStatus) -> bool:
        return target in self._transitions.get(current, set())

    def transition(self, current: TaskStatus, target: TaskStatus) -> TaskStatus:
        if not self.can_transition(current, target):
            raise ValueError(f"Invalid transition: {current.value} -> {target.value}")
        return target
