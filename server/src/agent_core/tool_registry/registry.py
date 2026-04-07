from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

ToolExecutor = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    mode: str
    executor: ToolExecutor


@dataclass(slots=True)
class ToolRegistry:
    _tools: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def list_tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "mode": spec.mode,
            }
            for spec in self._tools.values()
        ]

    def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        spec = self._tools.get(name)
        if not spec:
            raise KeyError(f"Unknown tool: {name}")
        return spec.executor(args)
