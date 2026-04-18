"""backend-task-core 最小对象模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_core.context.models import now_ms


@dataclass(slots=True)
class TaskRuntime:
    """最小任务实例模型。"""

    task_id: str
    task_type: str
    version: str
    session_id: str
    device_id: str
    state: str
    input: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at_ms: int = field(default_factory=now_ms)
    updated_at_ms: int = field(default_factory=now_ms)
    started_at_ms: int | None = None
    completed_at_ms: int | None = None
