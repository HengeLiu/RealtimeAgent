"""agent-core 统一工具注册表。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent_core.mcp import McpGateway, McpRegistry
from agent_core.models import ToolSpec
from agent_core.skills import SkillGateway, SkillRegistry
from agent_core.tools.base import AgentToolContext, BaseMcpTool, BaseSkillTool, BaseTool
from backend_task_core import InMemoryTaskGateway, TaskGateway
from infra.errors import ErrorCode, build_error

try:  # pragma: no cover - 真实 SDK 可选
    from agents import RunContextWrapper, function_tool
except ImportError:  # pragma: no cover - 单测环境兜底
    class RunContextWrapper:  # type: ignore[override]
        """最小 RunContextWrapper 兜底类型。"""

        def __class_getitem__(cls, _item):
            return cls

    def function_tool(*, name_override=None, failure_error_function=None):  # type: ignore[override]
        """无 agents 依赖时退化为原函数装饰器。"""

        def _decorator(func):
            func._tool_name_override = name_override
            func._tool_failure_error_function = failure_error_function
            return func

        return _decorator


class _GenericPayload(BaseModel):
    """SDK Tool 通用参数对象。"""

    payload: dict[str, Any] | None = Field(default=None, description="工具参数字典")


class SkillToolProxy(BaseSkillTool):
    """把 Skill 暴露成统一 Tool。"""

    def __init__(
        self,
        *,
        skill_name: str,
        skill_gateway: SkillGateway,
        description: str,
        input_model: type[BaseModel],
    ) -> None:
        self._skill_name = skill_name
        self._skill_gateway = skill_gateway
        self.spec = ToolSpec(
            name=skill_name,
            description=description,
            input_model=input_model,
            capability_type="skill",
            tags=["skill"],
        )

    def run(self, context: AgentToolContext, input_data):
        return self._skill_gateway.invoke(
            name=self._skill_name,
            context=context,
            arguments=input_data.model_dump(exclude_none=True),
            record_trace=False,
        )


class McpToolProxy(BaseMcpTool):
    """把 MCP 方法暴露成统一 Tool。"""

    def __init__(
        self,
        *,
        method_name: str,
        tool_name: str,
        mcp_gateway: McpGateway,
        description: str,
        input_model: type[BaseModel],
    ) -> None:
        self._method_name = method_name
        self._mcp_gateway = mcp_gateway
        self.spec = ToolSpec(
            name=tool_name,
            description=description,
            input_model=input_model,
            capability_type="mcp",
            tags=["mcp"],
        )

    def run(self, context: AgentToolContext, input_data):
        return self._mcp_gateway.invoke(
            name=self._method_name,
            context=context,
            arguments=input_data.model_dump(exclude_none=True),
            record_trace=False,
        )


class ToolRegistry:
    """统一 Tool 注册表。"""

    def __init__(
        self,
        *,
        device_state_reader,
        task_gateway: TaskGateway | None = None,
        skill_registry: SkillRegistry | None = None,
        skill_gateway: SkillGateway | None = None,
        mcp_registry: McpRegistry | None = None,
        mcp_gateway: McpGateway | None = None,
    ) -> None:
        self._device_state_reader = device_state_reader
        self._task_gateway = task_gateway or InMemoryTaskGateway()
        self._skill_registry = skill_registry or SkillRegistry()
        self._skill_gateway = skill_gateway or SkillGateway(self._skill_registry)
        self._mcp_registry = mcp_registry or McpRegistry()
        self._mcp_gateway = mcp_gateway or McpGateway(self._mcp_registry)
        self._tools: dict[str, BaseTool] = {}
        self._sdk_tools: dict[str, Any] = {}
        self._gateway = None
        self.discover_tools()

    def bind_gateway(self, gateway) -> None:
        """绑定统一 ToolGateway。"""

        self._gateway = gateway

    def discover_tools(self) -> None:
        """导入并注册内置 Function/Skill/MCP Tool。"""

        from agent_core.tools.builtins import (
            CancelTaskTool,
            CapturePhotoTool,
            CreateTimerTool,
            QueryDeviceStateTool,
            QueryTaskStatusTool,
        )

        for tool in (
            QueryDeviceStateTool(),
            CapturePhotoTool(),
            CreateTimerTool(),
            QueryTaskStatusTool(),
            CancelTaskTool(),
        ):
            self._register_tool(tool)

        for skill in self._skill_registry.list_skills():
            self._register_tool(
                SkillToolProxy(
                    skill_name=skill.spec.name,
                    skill_gateway=self._skill_gateway,
                    description=skill.spec.description,
                    input_model=skill.spec.input_model,
                )
            )

        for method in self._mcp_registry.list_methods():
            self._register_tool(
                McpToolProxy(
                    method_name=method.spec.name,
                    tool_name=method.spec.name.replace(".", "_"),
                    mcp_gateway=self._mcp_gateway,
                    description=method.spec.description,
                    input_model=method.spec.input_model,
                )
            )

    def _register_tool(self, tool: BaseTool) -> None:
        self._tools[tool.spec.name] = tool
        self._sdk_tools[tool.spec.name] = self._build_sdk_tool(tool.spec.name, tool.spec.description)

    def get(self, name: str) -> BaseTool | None:
        """按名称查询 Tool。"""

        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        """列出全部 Tool。"""

        return list(self._tools.values())

    def list_sdk_tools(self) -> list[Any]:
        """返回全部 SDK Tool。"""

        return [self._sdk_tools[name] for name in self._tools]

    def get_device_state_reader(self):
        """返回设备状态读取函数。"""

        return self._device_state_reader

    def get_task_gateway(self) -> TaskGateway:
        """返回任务网关。"""

        return self._task_gateway

    def get_skill_gateway(self) -> SkillGateway:
        """返回 SkillGateway。"""

        return self._skill_gateway

    def get_mcp_gateway(self) -> McpGateway:
        """返回 McpGateway。"""

        return self._mcp_gateway

    def invoke(self, *, name: str, context: AgentToolContext, arguments: dict[str, Any] | None = None):
        """兼容旧调用面，转发到 ToolGateway。"""

        gateway = self._gateway
        if gateway is None:
            from agent_core.tools.gateway import ToolGateway

            gateway = ToolGateway(self)
            self.bind_gateway(gateway)
        return gateway.invoke(name=name, context=context, arguments=arguments)

    def _build_sdk_tool(self, tool_name: str, description: str):
        @function_tool(
            name_override=tool_name,
            failure_error_function=lambda _ctx, exc: f"工具 {tool_name} 调用失败：{exc}",
        )
        def _sdk_tool(
            ctx: RunContextWrapper[AgentToolContext],
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """统一 SDK Tool 入口。"""

            gateway = self._gateway
            if gateway is None:
                raise build_error(
                    ErrorCode.INVALID_CONFIG,
                    "ToolGateway 尚未绑定到 ToolRegistry",
                    details={"tool_name": tool_name},
                )
            result = gateway.invoke(
                name=tool_name,
                context=ctx.context,
                arguments=payload or {},
            )
            return result.data

        _sdk_tool.__doc__ = description
        return _sdk_tool
