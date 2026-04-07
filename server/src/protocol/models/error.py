from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from protocol.enums import ErrorType
from protocol.models.base import Serializable


@dataclass(slots=True)
class ErrorModel(Serializable):
    error_code: str
    error_message: str
    error_type: ErrorType
    source: str
    retryable: bool
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ErrorModel":
        return cls(
            error_code=raw["error_code"],
            error_message=raw["error_message"],
            error_type=ErrorType(raw["error_type"]),
            source=raw["source"],
            retryable=bool(raw["retryable"]),
            details=dict(raw.get("details") or {}),
        )
