"""技能注册中心骨架实现。"""

from typing import Any

from nextgen.shared.contracts.registry import SkillRegistry


class ServerSkillRegistry(SkillRegistry):
    """服务器端技能注册中心。"""

    def __init__(self) -> None:
        """初始化技能注册中心。"""

        self.skills: dict[str, Any] = {}

    def register(self, name: str, skill: Any) -> None:
        """注册技能。

        参数：
        - name：技能名称
        - skill：技能对象
        """

        self.skills[name] = skill
