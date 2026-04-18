"""MCP 注册表。"""

from __future__ import annotations

from agent_core.mcp.base import RegisteredMcpMethod


class McpRegistry:
    """最小 MCP 方法注册表。"""

    def __init__(self) -> None:
        self._methods: dict[str, RegisteredMcpMethod] = {}
        self.discover_methods()

    def discover_methods(self) -> None:
        """导入并注册内置 MCP Adapter。"""

        from agent_core.mcp.adapters import AmapMcpAdapter

        adapter = AmapMcpAdapter(mock_mode=True)
        for spec in adapter.list_methods():
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
