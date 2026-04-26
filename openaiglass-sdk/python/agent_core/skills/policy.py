"""Skill 暴露策略。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SkillPolicy:
    """控制当前会话可使用哪些 Skill。

    主要功能：
    1. 支持按 Skill 名称设置允许列表。
    2. 在未设置允许列表时默认允许所有已注册 Skill。
    """

    allowed_skill_names: set[str] = field(default_factory=set)

    def is_allowed(self, skill_name: str) -> bool:
        """判断 Skill 是否允许使用。

        参数：
        1. `skill_name`：待判断的 Skill 名称。

        返回值：
        1. `True` 表示允许使用。
        2. `False` 表示当前策略不允许使用。
        """

        normalized = str(skill_name).strip()
        if not normalized:
            return False
        return not self.allowed_skill_names or normalized in self.allowed_skill_names

