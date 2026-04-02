"""注册中心抽象接口。"""

from abc import ABC, abstractmethod
from typing import Any


class SkillRegistry(ABC):
    """技能注册中心抽象接口。"""

    @abstractmethod
    def register(self, name: str, skill: Any) -> None:
        """注册技能。"""


class McpRegistry(ABC):
    """MCP 注册中心抽象接口。"""

    @abstractmethod
    def register(self, name: str, service: Any) -> None:
        """注册 MCP 服务。"""
