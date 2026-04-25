"""SDK 主入口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openaiglasses.capabilities import BaseTask, BaseTool, CapabilityRegistry
from openaiglasses.phone import BasePhoneTask, BaseSensorProvider, PhoneRuntime
from openaiglasses.runtime import DeviceGroupRuntime, TaskRuntimeManager

if TYPE_CHECKING:
    from agent_core import AgentFacade
    from api.http_server import ServerHandle
    from infra.config import ServerSettings


@dataclass(slots=True)
class OpenAIGlassesSDK:
    """OpenAI Glasses SDK 主入口。

    主要功能：
    1. 聚合能力注册表和设备组运行时。
    2. 为 example 或外部开发者项目提供统一装配入口。

    主要方法：
    1. `register_tool`：注册 Tool。
    2. `register_task`：注册 Task。
    3. `register_phone_processor`：注册手机处理器。
    4. `build_agent_facade`：构建真实服务端的 AgentFacade。
    5. `build_server_handle`：构建真实服务端句柄。
    6. `run_server`：以前台阻塞方式启动真实服务端。
    """

    registry: CapabilityRegistry = field(default_factory=CapabilityRegistry)
    device_groups: DeviceGroupRuntime = field(default_factory=DeviceGroupRuntime)
    task_runtime: TaskRuntimeManager = field(init=False)
    phone_runtime: PhoneRuntime = field(init=False)

    def __post_init__(self) -> None:
        """补齐运行时之间的引用关系。"""

        self.task_runtime = TaskRuntimeManager(
            registry=self.registry,
            device_groups=self.device_groups,
        )
        self.phone_runtime = PhoneRuntime(registry=self.registry)
        self.device_groups.task_runtime = self.task_runtime

    def register_tool(self, tool: BaseTool) -> None:
        """注册 Tool。"""

        self.registry.register_tool(tool)

    def register_task(self, task: BaseTask) -> None:
        """注册 Task。"""

        self.registry.register_task(task)

    def register_phone_processor(self, processor) -> None:
        """注册手机处理器。"""

        self.registry.register_phone_processor(processor)

    def register_phone_task(self, task: BasePhoneTask) -> None:
        """注册手机任务。"""

        self.registry.register_phone_task(task)

    def register_sensor_provider(self, provider: BaseSensorProvider) -> None:
        """注册传感器提供者。"""

        self.registry.register_sensor_provider(provider)

    def build_agent_facade(self, settings: "ServerSettings") -> "AgentFacade":
        """构建真实服务端可用的 AgentFacade。"""

        from openaiglasses.server import build_agent_facade_from_sdk

        return build_agent_facade_from_sdk(
            sdk=self,
            settings=settings,
        )

    def build_server_handle(self, settings: "ServerSettings") -> "ServerHandle":
        """构建真实服务端句柄。"""

        from openaiglasses.server import build_server_handle_from_sdk

        return build_server_handle_from_sdk(
            sdk=self,
            settings=settings,
        )

    def run_server(self, settings: "ServerSettings") -> "ServerHandle":
        """以前台阻塞方式启动真实服务端。

        返回值：
        1. 已启动的服务端句柄。

        异常情况：
        1. `KeyboardInterrupt` 时会优雅停止服务。
        """

        handle = self.build_server_handle(settings)
        try:
            handle.start()
            handle.thread.join()
        except KeyboardInterrupt:
            handle.stop()
        return handle

    def run(self, settings: "ServerSettings"):
        """兼容简化启动入口。"""

        return self.run_server(settings)
