from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SkillResult:
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    task_id: str | None = None
    error: dict[str, Any] | None = None
