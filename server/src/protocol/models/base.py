from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


@dataclass(slots=True)
class Serializable:
    """Minimal helper for dataclass <-> dict conversions."""

    def to_dict(self) -> dict[str, Any]:
        def _serialize(value: Any) -> Any:
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, list):
                return [_serialize(item) for item in value]
            if isinstance(value, dict):
                return {key: _serialize(val) for key, val in value.items()}
            if hasattr(value, "to_dict"):
                return value.to_dict()
            return value

        return _serialize(asdict(self))
