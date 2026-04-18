"""Skill 注册表。"""

from __future__ import annotations

from agent_core.skills.base import BaseSkill


class SkillRegistry:
    """最小 Skill 注册表。"""

    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}
        self.discover_skills()

    def discover_skills(self) -> None:
        """导入并注册内置 Skill。"""

        from agent_core.skills.builtins import PhotoInterpretSkill, TimerManageSkill

        for skill in (PhotoInterpretSkill(), TimerManageSkill()):
            self._skills[skill.spec.name] = skill

    def get(self, name: str) -> BaseSkill | None:
        """按名称查询 Skill。"""

        return self._skills.get(name)

    def list_skills(self) -> list[BaseSkill]:
        """列出全部 Skill。"""

        return list(self._skills.values())
