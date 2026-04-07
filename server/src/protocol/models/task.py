from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from protocol.enums import Priority, TaskSource, TaskStatus
from protocol.models.error import ErrorModel
from protocol.models.base import Serializable


@dataclass(slots=True)
class TaskModel(Serializable):
    task_id: str
    task_type: str
    source: TaskSource
    status: TaskStatus
    priority: Priority
    created_at: str
    updated_at: str
    task_name: str | None = None
    owner: str | None = None
    initiator_device_id: str | None = None
    target_device_ids: list[str] = field(default_factory=list)
    input: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: ErrorModel | None = None
    started_at: str | None = None
    ended_at: str | None = None
    parent_task_id: str | None = None
    worker_device_id: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TaskModel":
        error = raw.get("error")
        return cls(
            task_id=raw["task_id"],
            task_type=raw["task_type"],
            source=TaskSource(raw["source"]),
            status=TaskStatus(raw["status"]),
            priority=Priority(raw["priority"]),
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            task_name=raw.get("task_name"),
            owner=raw.get("owner"),
            initiator_device_id=raw.get("initiator_device_id"),
            target_device_ids=list(raw.get("target_device_ids") or []),
            input=dict(raw.get("input") or {}),
            context=dict(raw.get("context") or {}),
            result=dict(raw.get("result") or {}),
            error=ErrorModel.from_dict(error) if error else None,
            started_at=raw.get("started_at"),
            ended_at=raw.get("ended_at"),
            parent_task_id=raw.get("parent_task_id"),
            worker_device_id=raw.get("worker_device_id"),
        )
