"""回放时间轴与传感器工具。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openaiglasses.phone import BaseSensorProvider, SensorReading


@dataclass(slots=True)
class ReplayEvent:
    """回放时间轴事件。

    主要功能：
    1. 描述一个在指定时间点触发的回放动作。
    2. 统一承载帧输入、传感器输入、任务事件等测试输入。
    """

    at: int
    event_type: str
    payload: Any = None


@dataclass(slots=True)
class ReplayTimeline:
    """回放时间轴。

    主要功能：
    1. 从 JSON 资产或内存数据解析时间轴事件。
    2. 按时间顺序输出事件，供 `ScenarioRunner` 编排执行。
    """

    timeline_id: str
    time_unit: str = "ms"
    events: list[ReplayEvent] = field(default_factory=list)

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "ReplayTimeline":
        """从字典数据构建回放时间轴。"""

        raw_events = data.get("events")
        if not isinstance(raw_events, list):
            raise RuntimeError("回放时间轴 events 必须是数组")
        events: list[ReplayEvent] = []
        for item in raw_events:
            if not isinstance(item, dict):
                raise RuntimeError("回放时间轴中的事件项必须是对象")
            at = int(item.get("at") or 0)
            event_type = str(item.get("type") or "").strip()
            if not event_type:
                raise RuntimeError("回放时间轴事件 type 不能为空")
            events.append(
                ReplayEvent(
                    at=at,
                    event_type=event_type,
                    payload=item.get("payload"),
                )
            )
        events.sort(key=lambda item: item.at)
        return cls(
            timeline_id=str(data.get("timeline_id") or "timeline"),
            time_unit=str(data.get("time_unit") or "ms"),
            events=events,
        )

    @classmethod
    def load(cls, path: str | Path) -> "ReplayTimeline":
        """从 JSON 文件读取回放时间轴。"""

        file_path = Path(path)
        data = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"回放时间轴文件格式不正确: {file_path}")
        return cls.from_data(data)


class ReplaySensorProvider(BaseSensorProvider):
    """回放传感器提供者。

    主要功能：
    1. 让测试代码把传感器读数按时间轴写入 SDK。
    2. 让 `PhoneTaskContext.read_sensor(...)` 可以在普通开发机上消费这些读数。
    """

    def __init__(self, sensor_type: str) -> None:
        self.sensor_type = sensor_type
        self._readings: list[SensorReading] = []
        self._cursor = 0

    def append_reading(self, payload: dict[str, Any], *, timestamp_ms: int | None = None) -> SensorReading:
        """追加一条回放传感器读数。"""

        reading = SensorReading(
            sensor_type=self.sensor_type,
            payload=dict(payload),
            timestamp_ms=timestamp_ms,
        )
        self._readings.append(reading)
        return reading

    def read(self) -> SensorReading:
        """读取一条传感器回放数据。

        返回规则：
        1. 如果还有未消费的新读数，则按顺序消费下一条。
        2. 如果已全部消费，则返回最后一条读数，便于任务读取当前最新状态。
        """

        if not self._readings:
            raise RuntimeError(f"传感器回放数据为空: {self.sensor_type}")
        if self._cursor < len(self._readings):
            reading = self._readings[self._cursor]
            self._cursor += 1
            return reading
        return self._readings[-1]

    def snapshot(self) -> list[dict[str, Any]]:
        """导出当前回放传感器数据快照。"""

        return [
            {
                "sensor_type": item.sensor_type,
                "payload": dict(item.payload),
                "timestamp_ms": item.timestamp_ms,
            }
            for item in self._readings
        ]
