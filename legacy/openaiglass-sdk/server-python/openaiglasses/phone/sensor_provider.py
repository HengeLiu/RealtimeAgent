"""传感器提供者基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SensorReading:
    """传感器读数。"""

    sensor_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp_ms: int | None = None


class BaseSensorProvider(ABC):
    """传感器提供者基类。"""

    sensor_type: str = ""

    @abstractmethod
    def read(self) -> SensorReading:
        """读取一次传感器数据。"""
