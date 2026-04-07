from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from protocol.enums import SkillMode
from skill.base.skill_result import SkillResult


@dataclass(slots=True)
class SkillRequest:
    trace_id: str
    caller: str
    input: dict[str, Any] = field(default_factory=dict)


class SkillBase(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    mode: SkillMode

    @abstractmethod
    def execute(self, request: SkillRequest) -> SkillResult:
        raise NotImplementedError
