from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from protocol.enums import SkillCallStatus, SkillMode
from protocol.models.error import ErrorModel
from protocol.models.base import Serializable


@dataclass(slots=True)
class SkillCallModel(Serializable):
    skill_call_id: str
    skill_name: str
    caller: str
    mode: SkillMode
    status: SkillCallStatus
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    error: ErrorModel | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SkillCallModel":
        error = raw.get("error")
        return cls(
            skill_call_id=raw["skill_call_id"],
            skill_name=raw["skill_name"],
            caller=raw["caller"],
            mode=SkillMode(raw["mode"]),
            status=SkillCallStatus(raw["status"]),
            input=dict(raw.get("input") or {}),
            output=dict(raw.get("output") or {}),
            task_id=raw.get("task_id"),
            error=ErrorModel.from_dict(error) if error else None,
        )
