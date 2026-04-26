"""MCP 层基础定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from agent_core.models import CapabilityResult, McpMethodSpec
from agent_core.tools.base import AgentToolContext


@dataclass(slots=True)
class RegisteredMcpMethod:
    """注册完成的 MCP 方法。"""

    adapter_name: str
    spec: McpMethodSpec
    adapter: "BaseMcpAdapter"


class BaseMcpAdapter(ABC):
    """MCP 适配器基类。"""

    adapter_name: str

    @abstractmethod
    def list_methods(self) -> list[McpMethodSpec]:
        """返回当前适配器支持的方法列表。"""

    @abstractmethod
    def invoke(self, *, method_name: str, context: AgentToolContext, input_data) -> CapabilityResult:
        """调用指定方法。"""
