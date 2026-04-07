from __future__ import annotations

from dataclasses import dataclass, field

from skill.base import SkillBase, SkillRequest, SkillResult


@dataclass(slots=True)
class SkillRegistry:
    _skills: dict[str, SkillBase] = field(default_factory=dict)

    def register(self, skill: SkillBase) -> None:
        if skill.name in self._skills:
            raise ValueError(f"Skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> SkillBase:
        skill = self._skills.get(name)
        if not skill:
            raise KeyError(f"Unknown skill: {name}")
        return skill

    def list_skills(self) -> list[dict[str, object]]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "input_schema": s.input_schema,
                "output_schema": s.output_schema,
                "mode": s.mode.value,
            }
            for s in self._skills.values()
        ]

    def execute(self, name: str, request: SkillRequest) -> SkillResult:
        return self.get(name).execute(request)
