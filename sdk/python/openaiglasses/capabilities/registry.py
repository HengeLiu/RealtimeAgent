"""能力注册表。"""

from __future__ import annotations

from typing import Any

from openaiglasses.capabilities.base_task import BaseTask
from openaiglasses.capabilities.base_tool import BaseTool
from openaiglasses.phone import BasePhoneTask, BaseSensorProvider


class CapabilityRegistry:
    """SDK 能力注册表。

    主要功能：
    1. 保存开发者注册的 Tool、Task、手机处理器、手机任务和传感器提供者。
    2. 为运行时调度能力提供统一查询入口。

    主要方法：
    1. `register_tool`：注册短时工具。
    2. `register_task`：注册后台任务。
    3. `register_phone_processor`：注册手机处理器。
    4. `register_phone_task`：注册手机任务。
    5. `register_sensor_provider`：注册传感器提供者。
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._tasks: dict[str, BaseTask] = {}
        self._phone_processors: dict[str, Any] = {}
        self._phone_tasks: dict[str, BasePhoneTask] = {}
        self._sensor_providers: dict[str, BaseSensorProvider] = {}

    def register_tool(self, tool: BaseTool) -> None:
        """注册 Tool。

        参数：
        1. `tool`：开发者实现的 Tool。

        返回值：
        1. 无。

        异常情况：
        1. 工具名为空时抛出 `ValueError`。
        """

        if not tool.name:
            raise ValueError("tool.name 不能为空")
        self._tools[tool.name] = tool

    def register_task(self, task: BaseTask) -> None:
        """注册 Task。

        参数：
        1. `task`：开发者实现的 Task。

        返回值：
        1. 无。

        异常情况：
        1. 任务类型为空时抛出 `ValueError`。
        """

        if not task.task_type:
            raise ValueError("task.task_type 不能为空")
        self._tasks[task.task_type] = task

    def register_phone_processor(self, processor: Any) -> None:
        """注册手机处理器。

        参数：
        1. `processor`：开发者实现的手机处理器。

        返回值：
        1. 无。

        异常情况：
        1. 处理器类型为空时抛出 `ValueError`。
        """

        processor_type = getattr(processor, "processor_type", "")
        if not processor_type:
            raise ValueError("processor.processor_type 不能为空")
        self._phone_processors[processor_type] = processor

    def register_phone_task(self, task: BasePhoneTask) -> None:
        """注册手机任务。"""

        if not task.task_type:
            raise ValueError("phone_task.task_type 不能为空")
        self._phone_tasks[task.task_type] = task

    def register_sensor_provider(self, provider: BaseSensorProvider) -> None:
        """注册传感器提供者。"""

        if not provider.sensor_type:
            raise ValueError("sensor_provider.sensor_type 不能为空")
        self._sensor_providers[provider.sensor_type] = provider

    def get_tool(self, name: str) -> BaseTool | None:
        """按名称读取 Tool。"""

        return self._tools.get(name)

    def get_task(self, task_type: str) -> BaseTask | None:
        """按类型读取 Task。"""

        return self._tasks.get(task_type)

    def get_phone_processor(self, processor_type: str) -> Any | None:
        """按类型读取手机处理器。"""

        return self._phone_processors.get(processor_type)

    def get_phone_task(self, task_type: str) -> BasePhoneTask | None:
        """按类型读取手机任务。"""

        return self._phone_tasks.get(task_type)

    def get_sensor_provider(self, sensor_type: str) -> BaseSensorProvider | None:
        """按类型读取传感器提供者。"""

        return self._sensor_providers.get(sensor_type)

    def list_tool_names(self) -> list[str]:
        """列出全部 Tool 名称。"""

        return sorted(self._tools)

    def list_task_types(self) -> list[str]:
        """列出全部 Task 类型。"""

        return sorted(self._tasks)

    def list_phone_processor_types(self) -> list[str]:
        """列出全部手机处理器类型。"""

        return sorted(self._phone_processors)

    def list_phone_task_types(self) -> list[str]:
        """列出全部手机任务类型。"""

        return sorted(self._phone_tasks)

    def list_sensor_types(self) -> list[str]:
        """列出全部传感器类型。"""

        return sorted(self._sensor_providers)
