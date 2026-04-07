from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class McpRequest:
    action: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class McpResult:
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class McpAdapter(ABC):
    name: str
    description: str
    request_schema: dict[str, Any]
    response_schema: dict[str, Any]

    @abstractmethod
    def invoke(self, request: McpRequest) -> McpResult:
        raise NotImplementedError
