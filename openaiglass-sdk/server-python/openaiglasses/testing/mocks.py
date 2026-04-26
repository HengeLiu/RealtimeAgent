"""模拟设备运行时。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MockGlassRuntime:
    """模拟眼镜运行时。

    主要功能：
    1. 记录收到的控制命令。
    2. 为跨端回放测试提供眼镜端替身。

    主要属性：
    1. `device_id`：模拟眼镜编号。
    2. `commands`：收到的命令列表。
    """

    device_id: str
    commands: list[dict[str, Any]] = field(default_factory=list)
    frames: list[Any] = field(default_factory=list)

    def receive_command(self, name: str, payload: dict[str, Any] | None = None) -> None:
        """记录一条命令。"""

        self.commands.append({"name": name, "payload": payload or {}})

    def push_frame(self, frame: Any) -> None:
        """记录一帧回放输入。"""

        self.frames.append(frame)


@dataclass(slots=True)
class MockPhoneRuntime:
    """模拟手机运行时。

    主要功能：
    1. 记录启动的处理器。
    2. 为跨端回放测试提供手机端替身。
    """

    device_id: str
    processors: dict[str, Any] = field(default_factory=dict)
    commands: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    stopped_tasks: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)

    def register_processor(self, processor: Any) -> None:
        """注册一个模拟处理器。"""

        processor_type = getattr(processor, "processor_type", "")
        if not processor_type:
            raise ValueError("processor.processor_type 不能为空")
        self.processors[processor_type] = processor

    def receive_command(self, name: str, payload: dict[str, Any] | None = None) -> None:
        """记录一条手机侧命令。"""

        self.commands.append({"name": name, "payload": payload or {}})

    def start_task(self, *, task_type: str, params: dict[str, Any] | None = None) -> None:
        """记录一个手机任务启动动作。"""

        self.tasks.append(
            {
                "task_type": task_type,
                "params": dict(params or {}),
            }
        )

    def stop_task(self, task_id: str) -> None:
        """记录一个手机任务停止动作。"""

        self.stopped_tasks.append(task_id)

    def emit_result(self, result: dict[str, Any]) -> None:
        """记录一次手机侧结构化结果。"""

        self.results.append(dict(result))
