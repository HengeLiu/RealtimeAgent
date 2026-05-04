"""agent-core 统一工具注册表。"""

from __future__ import annotations

import inspect
from typing import Any

from pydantic import BaseModel

from agent_core.camera import CameraGateway, UtterancePhotoStore
from agent_core.memory import AgentMemoryRuntime
from agent_core.mcp import McpGateway, McpRegistry
from agent_core.models import ToolSpec, normalize_progress_messages
from agent_core.skills import SkillRuntime
from agent_core.tools.base import AgentToolContext, BaseMcpTool, BaseTool
from backend_task_core import InMemoryTaskGateway, TaskGateway
from infra.errors import ErrorCode, build_error

try:  # pragma: no cover - 真实 SDK 可选
    from agents import RunContextWrapper, function_tool
except ImportError:  # pragma: no cover - 单测环境兜底
    class RunContextWrapper:  # type: ignore[override]
        """最小 RunContextWrapper 兜底类型。"""

        def __class_getitem__(cls, _item):
            return cls

    def function_tool(*, name_override=None, failure_error_function=None, strict_mode=None):  # type: ignore[override]
        """无 agents 依赖时退化为原函数装饰器。"""

        def _decorator(func):
            func._tool_name_override = name_override
            func._tool_failure_error_function = failure_error_function
            func._tool_strict_mode = strict_mode
            return func

        return _decorator

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
        camera_gateway: CameraGateway | None = None,
        mcp_registry: McpRegistry | None = None,
        mcp_gateway: McpGateway | None = None,
        skill_runtime: SkillRuntime | None = None,
        memory_runtime: AgentMemoryRuntime | None = None,
    ) -> None:
        self._device_state_reader = device_state_reader
        self._task_gateway = task_gateway or InMemoryTaskGateway()
        self._camera_gateway = camera_gateway
        self._mcp_registry = mcp_registry or McpRegistry()
        self._mcp_gateway = mcp_gateway or McpGateway(self._mcp_registry)
        self._skill_runtime = skill_runtime
        self._memory_runtime = memory_runtime
        self._utterance_photo_store = UtterancePhotoStore()
        self._device_group_context_factory = None
        self._tools: dict[str, BaseTool] = {}
        self._sdk_tools: dict[str, Any] = {}
        self._model_tool_names: list[str] = []
        self._gateway = None
        self.discover_tools()

    def bind_gateway(self, gateway) -> None:
        """绑定统一 ToolGateway。"""

        self._gateway = gateway

    def bind_camera_gateway(self, camera_gateway: CameraGateway) -> None:
        """绑定相机抓拍网关。"""

        self._camera_gateway = camera_gateway

    def bind_device_state_reader(self, device_state_reader) -> None:
        """绑定设备运行态读取函数。"""

        self._device_state_reader = device_state_reader

    def bind_device_group_context_factory(self, factory) -> None:
        """绑定 DeviceGroupContext 构造工厂。"""

        self._device_group_context_factory = factory

    def discover_tools(self) -> None:
        """导入并注册内置 Tool 与 MCP Tool。"""

        from agent_core.tools.builtins import (
            CancelTaskTool,
            CapturePhotoTool,
            CloseContinuousDialogTool,
            ManageMemoryTool,
            MemorySearchTool,
            QueryDeviceStateTool,
            QueryTaskStatusTool,
            ReadSkillTool,
            StartPhoneVideoLinkTool,
        )

        for tool in (
            QueryDeviceStateTool(),
            CapturePhotoTool(),
            QueryTaskStatusTool(),
            CancelTaskTool(),
            StartPhoneVideoLinkTool(),
            CloseContinuousDialogTool(),
        ):
            self._register_tool(
                tool,
                expose_to_model=tool.spec.name == "close_continuous_dialog",
            )

        for method in self._mcp_registry.list_methods():
            self._register_tool(
                McpToolProxy(
                    method_name=method.spec.name,
                    tool_name=method.spec.name.replace(".", "_"),
                    mcp_gateway=self._mcp_gateway,
                    description=method.spec.description,
                    input_model=method.spec.input_model,
                ),
                expose_to_model=False,
            )

        if self._skill_runtime is not None:
            self._register_tool(
                ReadSkillTool(self._skill_runtime),
                expose_to_model=bool(self._skill_runtime.list_skill_names()),
            )
        if self._memory_runtime is not None and self._memory_runtime.enabled:
            self._register_tool(
                MemorySearchTool(self._memory_runtime),
                expose_to_model=True,
            )
            self._register_tool(
                ManageMemoryTool(self._memory_runtime),
                expose_to_model=True,
            )

    def _register_tool(self, tool: BaseTool, *, expose_to_model: bool) -> None:
        self._tools[tool.spec.name] = tool
        self._sdk_tools[tool.spec.name] = self._build_sdk_tool(
            tool_name=tool.spec.name,
            description=tool.spec.description,
            input_model=tool.spec.input_model,
        )
        if tool.spec.name in self._model_tool_names:
            self._model_tool_names.remove(tool.spec.name)
        if expose_to_model:
            self._model_tool_names.append(tool.spec.name)

    def register_external_tool(self, tool: BaseTool, *, expose_to_model: bool = True) -> None:
        """注册外部注入的 Tool。"""

        self._register_tool(tool, expose_to_model=expose_to_model)

    def get(self, name: str) -> BaseTool | None:
        """按名称查询 Tool。"""

        return self._tools.get(name)

    def list_tools(self, *, allowed_names: set[str] | None = None) -> list[BaseTool]:
        """列出当前对模型暴露的 Tool。"""

        names = self._filter_model_tool_names(allowed_names)
        return [self._tools[name] for name in names]

    def list_sdk_tools(self, *, allowed_names: set[str] | None = None) -> list[Any]:
        """返回当前对模型暴露的 SDK Tool。"""

        names = self._filter_model_tool_names(allowed_names)
        return [self._sdk_tools[name] for name in names]

    def list_progress_messages(self) -> list[tuple[str, str]]:
        """列出所有 Tool 声明的前置播报文案。

        返回值：
        1. `(tool_name, progress_message)` 列表，按工具名和候选文案顺序排序。

        异常情况：
        1. 本方法只读取注册表内存状态，不抛出业务异常。
        """

        messages: list[tuple[str, str]] = []
        for name, tool in sorted(self._tools.items()):
            for message in normalize_progress_messages(tool.spec.progress_message):
                messages.append((name, message))
        return messages

    def get_skill_runtime(self) -> SkillRuntime | None:
        """返回 Skill Runtime。"""

        return self._skill_runtime

    def get_memory_runtime(self) -> AgentMemoryRuntime | None:
        """返回 Agent 长期记忆运行时。"""

        return self._memory_runtime

    def is_tool_allowed_for_session(self, *, session_id: str, tool_name: str) -> bool:
        """判断指定会话是否允许调用工具。"""

        if self._skill_runtime is None:
            return True
        allowed_names = self._skill_runtime.allowed_tool_names_for_session(session_id=session_id)
        if allowed_names is None:
            return True
        return tool_name in allowed_names

    def get_device_state_reader(self):
        """返回设备状态读取函数。"""

        return self._device_state_reader

    def get_device_group_context_factory(self):
        """返回 DeviceGroupContext 工厂。"""

        return self._device_group_context_factory

    def get_task_gateway(self) -> TaskGateway:
        """返回任务网关。"""

        return self._task_gateway

    def get_camera_gateway(self) -> CameraGateway | None:
        """返回相机抓拍网关。"""

        return self._camera_gateway

    def get_utterance_photo_store(self) -> UtterancePhotoStore:
        """返回语音轮次自动抓拍缓存。"""

        return self._utterance_photo_store

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

    def _filter_model_tool_names(self, allowed_names: set[str] | None) -> list[str]:
        """按 Skill 白名单过滤模型可见工具。

        记忆管理和连续对话关闭属于全局用户控制能力，不随具体 Skill 白名单关闭；
        否则用户在某个 Skill 激活期间将无法删除错误记忆或退出连续对话。
        """

        if allowed_names is None:
            return list(self._model_tool_names)
        normalized = {str(item).strip() for item in allowed_names if str(item).strip()}
        return [
            name
            for name in self._model_tool_names
            if name in normalized or "memory" in self._tools[name].spec.tags or "system" in self._tools[name].spec.tags
        ]

    def _build_sdk_tool(
        self,
        *,
        tool_name: str,
        description: str,
        input_model: type[BaseModel],
    ):
        def _sdk_tool(
            ctx: RunContextWrapper[AgentToolContext],
            **kwargs,
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
                arguments=dict(kwargs),
            )
            return result.data

        _sdk_tool.__doc__ = description
        _sdk_tool.__signature__ = self._build_sdk_signature(input_model)  # type: ignore[attr-defined]
        _sdk_tool.__annotations__ = self._build_sdk_annotations(input_model)
        return function_tool(
            name_override=tool_name,
            failure_error_function=lambda _ctx, exc: f"工具 {tool_name} 调用失败：{exc}",
            strict_mode=False,
        )(_sdk_tool)

    @staticmethod
    def _build_sdk_signature(input_model: type[BaseModel]) -> inspect.Signature:
        """按输入模型动态生成 SDK Tool 签名。"""

        parameters = [
            inspect.Parameter(
                "ctx",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=RunContextWrapper[AgentToolContext],
            )
        ]
        for field_name, field in input_model.model_fields.items():
            default = inspect._empty if field.is_required() else field.default
            parameters.append(
                inspect.Parameter(
                    field_name,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=default,
                    annotation=field.annotation,
                )
            )
        return inspect.Signature(parameters=parameters, return_annotation=dict[str, Any])

    @staticmethod
    def _build_sdk_annotations(input_model: type[BaseModel]) -> dict[str, Any]:
        """按输入模型动态生成 SDK Tool 注解。"""

        annotations: dict[str, Any] = {
            "ctx": RunContextWrapper[AgentToolContext],
            "return": dict[str, Any],
        }
        for field_name, field in input_model.model_fields.items():
            annotations[field_name] = field.annotation
        return annotations
