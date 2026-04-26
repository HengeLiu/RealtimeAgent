"""手机长任务扩展基类。"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PhoneTaskContext:
    """手机长任务上下文。"""

    runtime: Any
    task_id: str
    params: dict[str, Any] = field(default_factory=dict)
    state: str = "created"
    data: dict[str, Any] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)

    def emit_state(self, state: str, data: dict[str, Any] | None = None) -> None:
        """更新手机任务状态。"""

        self.state = state
        if data:
            self.data.update(data)

    def emit_result(self, result: dict[str, Any]) -> None:
        """输出手机任务结果。"""

        self.results.append(result)

    def update(self, data: dict[str, Any]) -> None:
        """更新手机任务上下文数据。"""

        self.data.update(data)

    def process_frame(
        self,
        *,
        processor_type: str,
        frame: Any,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """通过手机处理器处理一帧输入。"""

        return self.runtime.process_with_processor(
            processor_type=processor_type,
            frame=frame,
            params=params or self.params,
        )

    def read_sensor(self, sensor_type: str):
        """读取一次传感器数据。"""

        return self.runtime.read_sensor(sensor_type)

    def query_self(self):
        """读取当前手机任务快照。

        返回值：
        1. 当前任务对应的 `PhoneTaskSnapshot`。

        异常情况：
        1. 当前任务已被运行时移除时，底层运行时会抛出异常。
        """

        return self.runtime.query_task(self.task_id)


class BasePhoneTask(ABC):
    """手机侧长生命周期任务基类。"""

    task_type: str = ""
    description: str = ""

    def on_start(self, context: PhoneTaskContext) -> None:
        """启动手机任务。"""

    def on_frame(self, context: PhoneTaskContext, frame: Any) -> None:
        """处理一帧输入。"""

    def on_stop(self, context: PhoneTaskContext) -> None:
        """停止手机任务。"""

        context.emit_state("stopped")
