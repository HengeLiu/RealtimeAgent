"""MCP 注册表。"""

from __future__ import annotations

from collections.abc import Iterable

from agent_core.mcp.base import BaseMcpAdapter
from agent_core.mcp.base import RegisteredMcpMethod


class McpRegistry:
    """最小 MCP 方法注册表。

    主要功能：
    1. 维护外部注册的 MCP adapter。
    2. 向 Tool 层暴露可调用的方法清单。
    3. 保持根服务端默认不携带具体业务 adapter。
    """

    def __init__(self, adapters: Iterable[BaseMcpAdapter] | None = None) -> None:
        self._methods: dict[str, RegisteredMcpMethod] = {}
        self.discover_methods(adapters or [])

    def discover_methods(self, adapters: Iterable[BaseMcpAdapter]) -> None:
        """注册传入的 MCP Adapter。

        参数：
        1. `adapters`：外部业务项目注入的 MCP adapter 列表。

        返回值：
        1. 无。

        异常情况：
        1. 方法重名时抛出 `ValueError`，避免后注册 adapter 静默覆盖。
        """

        for adapter in adapters:
            self.register_adapter(adapter)

    def register_adapter(self, adapter: BaseMcpAdapter) -> None:
        """注册单个 MCP Adapter。"""

        for spec in adapter.list_methods():
            if spec.name in self._methods:
                raise ValueError(f"MCP 方法重复注册: {spec.name}")
            self._methods[spec.name] = RegisteredMcpMethod(
                adapter_name=adapter.adapter_name,
                spec=spec,
                adapter=adapter,
            )

    def get(self, name: str) -> RegisteredMcpMethod | None:
        """按名称查询 MCP 方法。"""

        return self._methods.get(name)

    def list_methods(self) -> list[RegisteredMcpMethod]:
        """列出全部 MCP 方法。"""

        return list(self._methods.values())
