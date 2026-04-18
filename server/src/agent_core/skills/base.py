"""Skill 层基础定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agent_core.models import CapabilityResult, SkillSpec
from agent_core.tools.base import AgentToolContext


class BaseSkill(ABC):
    """Skill 基类。"""

    spec: SkillSpec

    @abstractmethod
    def run(self, context: AgentToolContext, input_data) -> CapabilityResult:
        """执行 Skill 逻辑。"""
