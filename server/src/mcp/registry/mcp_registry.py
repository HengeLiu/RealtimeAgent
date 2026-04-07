from __future__ import annotations

from dataclasses import dataclass, field

from mcp.base import McpAdapter, McpRequest, McpResult


@dataclass(slots=True)
class McpRegistry:
    _adapters: dict[str, McpAdapter] = field(default_factory=dict)

    def register(self, adapter: McpAdapter) -> None:
        if adapter.name in self._adapters:
            raise ValueError(f"MCP adapter already registered: {adapter.name}")
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> McpAdapter:
        adapter = self._adapters.get(name)
        if not adapter:
            raise KeyError(f"Unknown MCP adapter: {name}")
        return adapter

    def invoke(self, name: str, request: McpRequest) -> McpResult:
        return self.get(name).invoke(request)
