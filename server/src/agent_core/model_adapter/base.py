from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelOutput:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class ModelAdapter(ABC):
    @abstractmethod
    def generate(self, *, prompt: str, context: list[dict[str, Any]] | None = None) -> ModelOutput:
        raise NotImplementedError

    @abstractmethod
    def stream_generate(self, *, prompt: str, context: list[dict[str, Any]] | None = None) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def generate_with_tools(
        self,
        *,
        prompt: str,
        context: list[dict[str, Any]] | None,
        tools: list[dict[str, Any]],
    ) -> ModelOutput:
        raise NotImplementedError
