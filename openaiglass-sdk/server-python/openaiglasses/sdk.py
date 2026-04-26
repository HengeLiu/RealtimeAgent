"""SDK 主入口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openaiglasses.capabilities import BaseTask, BaseTool, CapabilityRegistry
from openaiglasses.phone import BasePhoneTask, BaseSensorProvider, PhoneRuntime
from openaiglasses.runtime import DeviceGroupRuntime, TaskRuntimeManager

if TYPE_CHECKING:
    from agent_core import AgentFacade
    from agent_core.mcp import BaseMcpAdapter
    from api.http_server import ServerHandle
    from infra.config import ServerSettings


@dataclass(slots=True)
class OpenAIGlassesSDK:
    """OpenAI Glasses SDK 主入口。

    主要功能：
    1. 聚合能力注册表和设备组运行时。
    2. 为 openaiglass-for-blind 或外部开发者项目提供统一装配入口。

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
    mcp_adapters: list["BaseMcpAdapter"] = field(default_factory=list)
    scenario_handlers: dict[str, object] = field(default_factory=dict)
    task_runtime: TaskRuntimeManager = field(init=False)
    phone_runtime: PhoneRuntime = field(init=False)
    _mcp_registry: object = field(init=False, repr=False)
    _mcp_gateway: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """补齐运行时之间的引用关系。"""

        from agent_core.mcp import McpGateway, McpRegistry

        self._mcp_registry = McpRegistry()
        self._mcp_gateway = McpGateway(self._mcp_registry)
        self.task_runtime = TaskRuntimeManager(
            registry=self.registry,
            device_groups=self.device_groups,
        )
        self.phone_runtime = PhoneRuntime(registry=self.registry)
        self.device_groups.task_runtime = self.task_runtime
        self.device_groups.bind_mcp_gateway(self._mcp_gateway)

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

    def register_scenario_handler(self, capability: str, handler: object) -> None:
        """注册场景回放处理器。

        功能：
        1. 让 SDK 的场景回放框架只保留通用机制。
        2. 让官方样例或外部项目把能力特定回放逻辑放在自身目录下。

        参数：
        1. `capability`：能力类型名称。
        2. `handler`：对应能力的场景处理器对象。
        """

        normalized = str(capability).strip()
        if not normalized:
            raise ValueError("scenario capability 不能为空")
        self.scenario_handlers[normalized] = handler

    def get_scenario_handler(self, capability: str) -> object | None:
        """读取已注册场景回放处理器。"""

        normalized = str(capability).strip()
        if not normalized:
            return None
        return self.scenario_handlers.get(normalized)

    def list_scenario_capabilities(self) -> list[str]:
        """列出当前已注册的场景能力类型。"""

        return sorted(self.scenario_handlers.keys())

    def register_mcp_adapter(self, adapter: "BaseMcpAdapter") -> None:
        """注册 MCP Adapter。

        功能：
        1. 让外部项目把地图、导航、搜索等业务 adapter 注入服务端运行时。
        2. 避免根服务端默认携带具体业务实现。

        参数：
        1. `adapter`：实现了 `BaseMcpAdapter` 的 adapter 实例。

        返回值：
        1. 无。

        异常情况：
        1. adapter 名称重复时抛出 `ValueError`。
        """

        adapter_name = str(getattr(adapter, "adapter_name", "")).strip()
        if not adapter_name:
            raise ValueError("MCP adapter_name 不能为空")
        if any(str(getattr(item, "adapter_name", "")).strip() == adapter_name for item in self.mcp_adapters):
            raise ValueError(f"MCP Adapter 重复注册: {adapter_name}")
        self._mcp_registry.register_adapter(adapter)
        self.mcp_adapters.append(adapter)

    def get_mcp_registry(self):
        """返回 SDK 统一 MCP 注册表。"""

        return self._mcp_registry

    def get_mcp_gateway(self):
        """返回 SDK 统一 MCP 调用网关。"""

        return self._mcp_gateway

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
