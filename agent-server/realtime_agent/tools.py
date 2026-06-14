from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import pkgutil
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from urllib import error as urllib_error
from urllib import request as urllib_request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, ValidationError, model_validator

from realtime_agent.asset import ArtifactRef, AssetRef
from realtime_agent.control import PublishResult
from realtime_agent.errors import RealtimeAgentError, ErrorCode
from realtime_agent.memory import memory_record_to_public_dict
from realtime_agent.mcp import McpGateway, McpServerSpec, McpToolSpec
from realtime_agent.protocol import SERVER_PRODUCER_ID, Event, EventName, StreamChunk, StreamFormat, StreamType, new_id
from realtime_agent.tool_run import (
    TOOL_RUN_BACKGROUND_TIMEOUT_SECONDS,
    ToolRun,
    ToolRunRunner,
    ToolRunStore,
)


class DeviceNotFoundError(RealtimeAgentError):
    """设备未找到异常。

    主要功能：当 typed device API 无法为当前用户找到支持指定能力的在线设备时抛出。
    """


class AmbiguousDeviceError(RealtimeAgentError):
    """设备选择不唯一异常。

    主要功能：当创建输入流或远程长命令时匹配到多台设备，要求调用方补充 selector。
    """


class DeviceBusyError(RealtimeAgentError):
    """设备忙异常。"""


class CapabilityNotSupportedError(RealtimeAgentError):
    """设备能力不支持异常。"""


class StreamTimeoutError(RealtimeAgentError):
    """数据流超时异常。"""


class CommandFailedError(RealtimeAgentError):
    """设备命令失败异常。"""


class PlaybackRejectedError(RealtimeAgentError):
    """播放请求被拒绝异常。"""


@dataclass(frozen=True)
class VisualAssetRef:
    """Tool 返回的视觉资产消费描述。

    主要功能：显式告诉 Agent Core 某个资产是否可以 append 给主模型，避免继续把
    `ToolResult.assets` 当作自动看图依据。
    主要属性：`visibility` 控制主模型是否可见原图，`consumer` 描述消费路径，
    `text_context` 是 append 时配套给模型的文字说明。
    """

    asset: AssetRef
    visibility: Literal["append_to_agent", "internal_only"] = "internal_only"
    consumer: Literal["agent_inline", "tool_internal", "task_runtime"] = "tool_internal"
    text_context: str = ""
    claim_required: bool = True


@dataclass(frozen=True)
class ToolResult:
    """Tool 执行结果。

    主要功能：作为 ToolGateway 返回给 Agent Core 的稳定结构，避免直接暴露任意异常。
    主要属性：`ok` 标识成功与否，`data/message/assets/visual_assets/artifacts/tasks/meta/error`
    是公开冻结字段。
    """

    ok: bool
    data: Any = None
    message: str = ""
    assets: list[AssetRef] | None = None
    visual_assets: list[VisualAssetRef] | None = None
    artifacts: list[ArtifactRef] | None = None
    tasks: list[Any] | None = None
    meta: dict | None = None
    error: dict | None = None
    status: Literal["completed", "running"] = "completed"

    @classmethod
    def running(
        cls,
        *,
        run_id: str,
        tool_name: str,
        message: str = "",
        meta: dict | None = None,
    ) -> "ToolResult":
        """创建“工具已启动仍在处理”的结构化结果。

        主要逻辑：等待窗口超时且 Tool 声明 background 时返回该结果，让当前 turn
        不再阻塞；模型据此告知用户正在处理，最终结果稍后由 follow-up 机制送达。
        参数：`run_id` 为可追踪 Tool Run 标识；`tool_name` 为工具名。
        返回值：`ToolResult`，`status="running"`。
        异常情况：无。
        """

        run_meta = dict(meta or {})
        run_meta.setdefault("tool_run_id", run_id)
        return cls(
            ok=True,
            status="running",
            data={"tool_run_id": run_id, "tool_name": tool_name, "status": "running"},
            message=message or TOOL_RUNNING_RESULT_MESSAGE,
            assets=[],
            visual_assets=[],
            artifacts=[],
            tasks=[],
            meta=run_meta,
            error=None,
        )

    @classmethod
    def success(
        cls,
        data: Any = None,
        *,
        message: str = "",
        assets: list[AssetRef] | None = None,
        visual_assets: list[VisualAssetRef] | None = None,
        artifacts: list[ArtifactRef] | None = None,
        tasks: list[Any] | None = None,
        meta: dict | None = None,
    ) -> "ToolResult":
        """创建成功结果。

        主要逻辑：把 Tool 的业务数据、资产、产物、任务引用和元数据归一成公开结构。
        参数：`data` 为模型或后续组件可读取的业务数据。
        返回值：`ToolResult`。
        异常情况：无。
        """
        return cls(
            ok=True,
            data=data,
            message=message,
            assets=assets or [],
            visual_assets=visual_assets or [],
            artifacts=artifacts or [],
            tasks=tasks or [],
            meta=meta or {},
            error=None,
        )

    @classmethod
    def failed(cls, error: RealtimeAgentError) -> "ToolResult":
        """创建失败结果。

        主要逻辑：把 SDK 异常转成稳定错误字典。
        参数：`error` 为 `RealtimeAgentError`。
        返回值：`ToolResult`。
        异常情况：无。
        """
        error_dict = error.to_dict()
        message = str(error_dict.get("message") or error)
        return cls(ok=False, message=message, assets=[], visual_assets=[], artifacts=[], tasks=[], meta={}, error=error_dict)

    @property
    def content(self) -> Any:
        """返回 Tool 结果主体数据。"""

        return self.data

    @property
    def metadata(self) -> dict:
        """返回 Tool 结果附加信息。"""

        return dict(self.meta or {})


class ToolError(RealtimeAgentError):
    """Tool 执行异常。

    主要功能：让业务 Tool 用稳定错误码表达参数错误、能力缺失或外部依赖失败。
    """


@dataclass(frozen=True)
class ToolTrace:
    """Tool 调用轨迹。

    主要功能：记录 Agent Core 通过 ToolGateway 看到的工具名、入参、耗时、结果和错误。
    主要属性：`trace_id` 用于串联一次调用，`ok` 表示调用是否成功。
    """

    trace_id: str
    tool_name: str
    user_id: str
    session_id: str | None
    input_data: dict
    ok: bool
    duration_ms: int
    result_message: str = ""
    error: dict | None = None
    created_at: float = 0.0


CapabilityTrace = ToolTrace


ProgressMessage = str | tuple[str, ...] | list[str]

TOOL_DEFAULT_TIMEOUT_SECONDS = 3.0
TOOL_MAX_WAIT_TIMEOUT_SECONDS = 3.0
TOOL_MAX_TIMEOUT_SECONDS = TOOL_MAX_WAIT_TIMEOUT_SECONDS

# late result 等待窗口；background 工具超过该窗口未完成时返回“运行中”结果转后台。
TOOL_WAIT_WINDOW_SECONDS = TOOL_MAX_WAIT_TIMEOUT_SECONDS

# 这些工具强依赖活跃 session 的同步语义（关闭会话 / 图片回填），禁止声明 background。
BACKGROUND_FORBIDDEN_TOOL_NAMES = {"close_audio_session", "capture_photo"}

# “运行中”结果默认文案；约束模型告知用户正在处理且不要重复调用、不念内部标识。
TOOL_RUNNING_RESULT_MESSAGE = (
    "工具已启动，仍在后台处理，结果稍后送达。请先用一句话告诉用户正在处理，"
    "不要重复调用该工具，也不要向用户提及任何内部标识。"
)


@dataclass(frozen=True)
class ToolSpec:
    """Tool 元数据声明。

    主要功能：让开发者用一处声明告诉 SDK 和模型：工具名、说明、输入参数模型、
    输出模型、类型标签和前置播报文案。
    主要属性：`input_model` 推荐使用 Pydantic BaseModel，字段类型和 Field 描述会
    自动转换为 provider function calling schema。
    """

    name: str
    description: str
    input_model: Any = dict
    output_model: Any = None
    capability_type: Literal["tool", "mcp", "task"] = "tool"
    tags: list[str] = field(default_factory=list)
    progress_message: ProgressMessage | None = None
    timeout_seconds: float | None = None
    late_result_policy: Literal["background", "fail_fast", "forbidden"] = "fail_fast"
    background_timeout_seconds: float | None = None
    follow_up_ttl_seconds: float | None = None
    allow_concurrent_runs: bool = False
    # late result 回流通道：model 注入模型组织回复；direct 经 Output Service 直通播报。
    late_result_notify: Literal["model", "direct"] = "model"
    # 是否允许取消后台运行；取消能力经 tool_run_manager 暴露给模型。
    cancel_supported: bool = False
    # 同会话同名 background 运行的实例上限；None 表示沿用默认去重（至多一个）。
    max_running_per_user: int | None = None
    # 超窗返回“运行中”结果时使用的工具自定义文案，替代统一默认文案。
    running_message: str | None = None


@dataclass
class ToolContext:
    """Tool 执行上下文。

    主要功能：由 SDK 注入用户、会话、设备、输出和资产上下文；Tool 不自行构造。
    公开边界：普通业务 Tool 通过显式设备 API、输出 API、资产 API 和受控服务
    门面完成动作；后台任务运行时仍只通过系统 Tool 暴露。
    """

    user_id: str
    session_id: str
    devices: "ToolDeviceFacade"
    output: Any = None
    assets: Any = None
    memory: Any = None
    skills: Any = None
    mcp: Any = None
    metadata: dict = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


@dataclass
class SystemToolContext(ToolContext):
    """系统内置 Tool 执行上下文。

    主要功能：只给 SDK 自带的运行时 Tool 使用，让 tool_run_manager、
    memory_search、read_skill、mcp_call 等工具通过可见 Tool 边界访问内部服务。
    普通业务 Tool 不会拿到这些属性，避免在 Tool 内绕过模型可见工具列表。
    """

    tool_runs: Any = None
    memory: Any = None
    skills: Any = None
    mcp: Any = None


class BaseTool:
    """业务 Tool 基类。

    主要功能：定义稳定 Tool 扩展面，自动发现只注册继承该类的具体子类。
    主要方法：`run()` 由 ToolExecutor 调用，子类必须覆盖。
    """

    name: str = ""
    description: str = ""
    input_model: Any = dict
    output_model: Any = None
    timeout_seconds: float | None = None
    progress_message: str = ""
    progress_messages: tuple[str, ...] = ()
    spec: ToolSpec | None = None

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行 Tool。

        主要逻辑：基类只声明接口，具体业务 Tool 覆盖实现。
        参数：`context` 为 SDK 注入上下文，`input_data` 为模型参数。
        返回值：`ToolResult`。
        异常情况：未覆盖时抛出 `ToolError`。
        """
        raise ToolError(f"tool {self.__class__.__name__} does not implement run()", code=ErrorCode.PROTOCOL_ERROR)

    def resolved_spec(self) -> ToolSpec:
        """返回工具的规范化元数据。

        主要逻辑：优先使用开发者声明的 `spec = ToolSpec(...)`；没有 spec 时支持 `name/description/input_model` 简写。
        参数：无。
        返回值：`ToolSpec`。
        异常情况：工具名为空时由注册表继续抛出结构化错误。
        """

        if isinstance(self.spec, ToolSpec):
            return self.spec
        progress: ProgressMessage | None = None
        if self.progress_messages:
            progress = self.progress_messages
        elif self.progress_message:
            progress = self.progress_message
        return ToolSpec(
            name=self.name or self.__class__.__name__,
            description=self.description,
            input_model=self.input_model,
            output_model=self.output_model,
            progress_message=progress,
            timeout_seconds=self.timeout_seconds,
        )


class ToolRegistry:
    """Tool 注册表。

    主要功能：保存工具名到 Tool 实例的映射，供 ToolGateway 发现和执行。
    主要方法：`register()`、`get()`、`list_tools()`。
    """

    def __init__(self, *, default_timeout_seconds: float = TOOL_DEFAULT_TIMEOUT_SECONDS, max_wait_timeout_seconds: float = TOOL_MAX_WAIT_TIMEOUT_SECONDS) -> None:
        self.default_timeout_seconds = float(default_timeout_seconds)
        self.max_wait_timeout_seconds = float(max_wait_timeout_seconds)
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册 Tool 实例。

        主要逻辑：使用 tool.name 或类名作为稳定名称。
        参数：`tool` 为 BaseTool 子类实例。
        返回值：无。
        异常情况：名称为空时抛出 `ToolError`。
        """
        spec = tool.resolved_spec()
        name = spec.name
        if not name:
            raise ToolError("tool name is required", code=ErrorCode.INVALID_ARGUMENT)
        if name in self._tools:
            raise ToolError(f"duplicate tool name: {name}", code=ErrorCode.PROTOCOL_ERROR)
        _validate_tool_timeout(
            spec,
            default_timeout_seconds=self.default_timeout_seconds,
            max_wait_timeout_seconds=self.max_wait_timeout_seconds,
        )
        tool.name = name
        tool.description = spec.description
        tool.input_model = spec.input_model
        tool.output_model = spec.output_model
        tool.timeout_seconds = spec.timeout_seconds
        self._tools[name] = tool

    def get(self, name: str) -> BaseTool:
        if name not in self._tools:
            raise ToolError(f"unknown tool: {name}", code=ErrorCode.NOT_FOUND)
        return self._tools[name]

    def list_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def list_names(self) -> list[str]:
        """列出已注册 Tool 名称。"""

        return sorted(self._tools)


class ToolAutoDiscovery:
    """Tool 自动发现器。

    主要功能：扫描配置包中继承 `BaseTool` 的具体类并注册。
    """

    def __init__(self) -> None:
        self.errors: list[dict[str, str]] = []

    def discover(self, packages: list[str], *, recursive: bool = False, fail_fast: bool = True) -> list[BaseTool]:
        """扫描 Tool 包。

        主要逻辑：导入每个包，按需递归扫描子模块，注册非内部、非抽象的 Tool 类。
        参数：`packages` 为模块路径列表。
        返回值：Tool 实例列表。
        异常情况：导入失败和重复名称按 `fail_fast` 决定抛出或记录到 `errors`。
        """
        tools: list[BaseTool] = []
        seen: dict[str, str] = {}
        for package in packages:
            for module in self._iter_modules(package, recursive=recursive, fail_fast=fail_fast):
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if not self._is_concrete_tool(name, obj):
                        continue
                    tool = obj()
                    spec = tool.resolved_spec()
                    tool_name = spec.name
                    tool.name = spec.name
                    tool.description = spec.description
                    tool.input_model = spec.input_model
                    tool.output_model = spec.output_model
                    tool.timeout_seconds = spec.timeout_seconds
                    owner = f"{obj.__module__}.{obj.__name__}"
                    previous = seen.get(tool_name)
                    if previous is not None:
                        error = {
                            "package": package,
                            "module": obj.__module__,
                            "error": f"duplicate tool name: {tool_name}",
                            "previous": previous,
                            "current": owner,
                        }
                        if fail_fast:
                            raise ToolError(error["error"], code=ErrorCode.PROTOCOL_ERROR, details=error)
                        self.errors.append(error)
                        continue
                    seen[tool_name] = owner
                    tools.append(tool)
        return tools

    def _iter_modules(self, package: str, *, recursive: bool, fail_fast: bool):
        try:
            root = importlib.import_module(package)
            yield root
        except Exception as exc:
            self._handle_import_error(package, package, exc, fail_fast)
            return
        if not recursive:
            return
        package_paths = getattr(root, "__path__", None)
        if package_paths is None:
            return
        prefix = f"{root.__name__}."
        for info in pkgutil.walk_packages(package_paths, prefix):
            try:
                yield importlib.import_module(info.name)
            except Exception as exc:
                self._handle_import_error(package, info.name, exc, fail_fast)

    def _handle_import_error(self, package: str, module: str, exc: Exception, fail_fast: bool) -> None:
        error = {"package": package, "module": module, "error": f"{type(exc).__name__}: {exc}"}
        if fail_fast:
            raise ToolError("tool discovery import failed", code=ErrorCode.PROTOCOL_ERROR, details=error) from exc
        self.errors.append(error)

    @staticmethod
    def _is_concrete_tool(name: str, obj: type) -> bool:
        return (
            name
            and not name.startswith("_")
            and obj is not BaseTool
            and issubclass(obj, BaseTool)
            and not inspect.isabstract(obj)
        )


class ToolPolicy:
    """Tool 调用策略。

    主要功能：按 allowlist / denylist 判断当前工具是否允许执行。
    """

    def __init__(self, *, allowlist: list[str] | None = None, denylist: list[str] | None = None) -> None:
        self.allowlist = set(allowlist or [])
        self.denylist = set(denylist or [])

    def allowed(self, tool_name: str) -> bool:
        if tool_name in self.denylist:
            return False
        return not self.allowlist or tool_name in self.allowlist


class ToolSchemaBuilder:
    """Tool schema 构造器。

    主要功能：把 BaseTool 元数据转换成 Agent Core 可传给 provider 的工具说明。
    """

    def __init__(self, *, default_timeout_seconds: float = TOOL_DEFAULT_TIMEOUT_SECONDS, max_wait_timeout_seconds: float = TOOL_MAX_WAIT_TIMEOUT_SECONDS) -> None:
        self.default_timeout_seconds = float(default_timeout_seconds)
        self.max_wait_timeout_seconds = float(max_wait_timeout_seconds)

    def build(self, tool: BaseTool) -> dict:
        """构造 SDK 内部工具 schema。

        主要逻辑：优先读取 Pydantic `model_json_schema()`，否则使用通用 object schema。
        参数：`tool` 为工具实例。
        返回值：包含 `name/description/parameters` 的字典。
        异常情况：schema 构造失败时降级为空 object schema。
        """

        spec = tool.resolved_spec()
        return {
            "name": spec.name,
            "description": spec.description,
            "parameters": self._input_schema(spec.input_model),
            "output_schema": self._output_schema(spec.output_model),
            "capability_type": spec.capability_type,
            "tags": list(spec.tags),
            "progress_message": _first_progress_message(spec.progress_message),
            "progress_messages": _progress_candidates(spec.progress_message),
            "timeout_seconds": _effective_tool_timeout(
                spec,
                default_timeout_seconds=self.default_timeout_seconds,
                max_wait_timeout_seconds=self.max_wait_timeout_seconds,
            ),
        }

    def build_provider_schema(self, tool: BaseTool) -> dict:
        """构造 provider function calling schema。"""

        schema = self.build(tool)
        return {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema["parameters"],
            },
        }

    def _input_schema(self, input_model: Any) -> dict:
        if isinstance(input_model, dict):
            return dict(input_model)
        schema_builder = getattr(input_model, "model_json_schema", None)
        if callable(schema_builder):
            try:
                schema = dict(schema_builder())
                schema.setdefault("type", "object")
                schema.setdefault("properties", {})
                return schema
            except Exception:
                return {"type": "object", "properties": {}, "additionalProperties": True}
        if input_model is dict or input_model is None:
            return {"type": "object", "properties": {}, "additionalProperties": True}
        return {"type": "object", "properties": {}, "additionalProperties": True}

    def _output_schema(self, output_model: Any) -> dict | None:
        if output_model is None:
            return None
        if isinstance(output_model, dict):
            return dict(output_model)
        schema_builder = getattr(output_model, "model_json_schema", None)
        if callable(schema_builder):
            try:
                return dict(schema_builder())
            except Exception:
                return None
        return None


class ToolContextFactory:
    """ToolContext 工厂。

    主要功能：集中创建 Tool 执行上下文，确保设备通讯能力只通过 DeviceRuntime 注入。
    """

    def __init__(
        self,
        *,
        app,
        memory_service: Any = None,
        skill_service: Any = None,
        mcp_gateway: Any = None,
    ) -> None:
        self.app = app
        self.memory_service = memory_service
        self.skill_service = skill_service
        self.mcp_gateway = mcp_gateway
        self.tool_run_admin: Any = None

    def bind_tool_run_admin(self, tool_run_admin: Any) -> None:
        """绑定 Tool Run 管理门面，供 tool_run_manager 工具使用。"""

        self.tool_run_admin = tool_run_admin

    def create(self, *, user_id: str, session_id: str, tool_name: str | None = None) -> ToolContext:
        context_cls = SystemToolContext if tool_name in SYSTEM_CONTEXT_TOOL_NAMES else ToolContext
        # background 能力可以发起持续 stream 和长命令；前台短工具维持短生命周期门面。
        allow_long_running = self._tool_allows_long_running(tool_name)
        internal_devices = DeviceRuntime(user_id=user_id, app=self.app, allow_long_running=allow_long_running)
        kwargs = {
            "metadata": {
                "app_name": getattr(getattr(self.app, "config", None), "app_name", ""),
                "runs_root": str(getattr(getattr(self.app, "recorder", None), "runs_root", "")),
            }
        }
        kwargs.update({"memory": self.memory_service, "skills": self.skill_service, "mcp": self.mcp_gateway})
        if context_cls is SystemToolContext:
            kwargs.update({"tool_runs": self.tool_run_admin})
        device_facade = BackgroundDeviceFacade(context=internal_devices) if allow_long_running else ToolDeviceFacade(context=internal_devices)
        return context_cls(
            user_id=user_id,
            session_id=session_id,
            devices=device_facade,
            output=OutputFacade(user_id=user_id, app=self.app),
            assets=AssetFacade(user_id=user_id, app=self.app),
            **kwargs,
        )

    def _tool_allows_long_running(self, tool_name: str | None) -> bool:
        """判断工具是否声明了 background 策略，从而需要长命令/持续 stream 能力。"""

        if not tool_name:
            return False
        gateway = getattr(self.app, "tool_gateway", None)
        registry = getattr(gateway, "registry", None)
        if registry is None:
            return False
        try:
            spec = registry.get(tool_name).resolved_spec()
        except Exception:  # noqa: BLE001 - 未知工具回退到短生命周期门面
            return False
        return spec.late_result_policy == "background"


class ToolExecutor:
    """Tool 执行器。

    主要功能：把每次工具调用建模成可追踪 Tool Run，在后台 runner 上执行 `tool.run()`，
    并以等待窗口统一前台短任务和长耗时能力：窗口内完成像现在一样返回最终结果；
    `background` 工具超窗则返回“运行中”结果转后台，late result 由 follow-up 机制送达。

    主要属性：`store` 持有 Tool Run 状态与 CAS 裁决；`runner` 为后台执行循环；
    `on_background_complete` 是后台完成回调（Phase 4 由 FollowUpRouter 注入）。
    """

    def __init__(
        self,
        *,
        default_timeout_seconds: float = TOOL_DEFAULT_TIMEOUT_SECONDS,
        max_wait_timeout_seconds: float = TOOL_MAX_WAIT_TIMEOUT_SECONDS,
        store: ToolRunStore | None = None,
        runner: ToolRunRunner | None = None,
        recorder: Any = None,
        on_background_complete: Any = None,
    ) -> None:
        self.default_timeout_seconds = float(default_timeout_seconds)
        self.max_wait_timeout_seconds = float(max_wait_timeout_seconds)
        self.wait_window_seconds = float(max_wait_timeout_seconds)
        self.store = store or ToolRunStore()
        self.runner = runner or ToolRunRunner()
        self.recorder = recorder
        self.on_background_complete = on_background_complete

    async def execute(self, tool: BaseTool, context: ToolContext, input_data: dict) -> ToolResult:
        """执行一次工具调用并按等待窗口返回结果。

        主要逻辑：
        1. 校验入参；`background` 工具先做同会话同名运行去重。
        2. 创建 Tool Run，提交后台 runner，等待窗口内完成则 CAS `completed_inline`。
        3. 超窗：`fail_fast/forbidden` 取消并失败；`background` CAS `reported_running`
           返回“运行中”结果，后台完成后由 done 回调 CAS `completed_late/failed`。
        参数：`tool` 为工具实例；`context` 为执行上下文；`input_data` 为模型入参。
        返回值：`ToolResult`（可能是最终结果或“运行中”结果）。
        异常情况：入参校验失败返回结构化失败；其余异常归一为失败结果。
        """

        spec = tool.resolved_spec()
        try:
            validated_input = self._validate_input(tool, input_data)
        except ValidationError as exc:
            return ToolResult.failed(
                ToolError(
                    "tool input validation failed",
                    code=ErrorCode.INVALID_ARGUMENT,
                    details={"errors": exc.errors()},
                )
            )
        policy = spec.late_result_policy
        running_message = spec.running_message or ""
        if policy == "background" and not spec.allow_concurrent_runs:
            running_count = self.store.count_active_by_tool(
                user_id=context.user_id,
                session_id=context.session_id,
                tool_name=spec.name,
            )
            allowed = spec.max_running_per_user if spec.max_running_per_user and spec.max_running_per_user > 0 else 1
            if running_count >= allowed:
                existing = self.store.find_active_by_tool(
                    user_id=context.user_id,
                    session_id=context.session_id,
                    tool_name=spec.name,
                )
                if existing is not None:
                    self._record(context.session_id, {"event": "tool_run.dedupe.reused", "tool_run_id": existing.run_id, "tool_name": spec.name})
                    return ToolResult.running(run_id=existing.run_id, tool_name=spec.name, message=running_message)

        background_timeout = (
            _background_timeout_for(tool, spec, wait_window=self.wait_window_seconds, input_data=input_data)
            if policy == "background"
            else None
        )
        run = ToolRun.create(
            tool_name=spec.name,
            user_id=context.user_id,
            session_id=context.session_id,
            result_policy=policy,
            input_data=dict(input_data or {}),
            background_timeout_seconds=background_timeout,
            follow_up_ttl_seconds=spec.follow_up_ttl_seconds if policy == "background" else None,
            metadata={"cancel_supported": bool(spec.cancel_supported), "notify_policy": spec.late_result_notify},
        )
        self.store.put(run)
        self._record(context.session_id, {"event": "tool_run.created", "tool_run_id": run.run_id, "tool_name": spec.name, "result_policy": policy})

        coro = tool.run(context, validated_input)
        if background_timeout is not None:
            # 后台总超时强制：到点向协程注入 TimeoutError，工具在 finally 清理。
            coro = asyncio.wait_for(coro, timeout=background_timeout)
        cf_future = self.runner.submit(user_id=context.user_id, coro=coro, run_id=run.run_id)
        wrapped = asyncio.wrap_future(cf_future)
        done, _pending = await asyncio.wait({wrapped}, timeout=self.wait_window_seconds)

        if wrapped in done:
            result = self._result_from_future(wrapped)
            self.store.try_transition(
                run.run_id,
                from_states={"running"},
                to_state="completed_inline",
                result=_serialize_tool_result(result),
            )
            self._record(context.session_id, {"event": "tool_run.completed_inline", "tool_run_id": run.run_id, "tool_name": spec.name, "ok": result.ok})
            return result

        # 等待窗口超时：tool.run 仍在后台执行。
        if policy != "background":
            cf_future.cancel()
            self._swallow_orphan(wrapped)
            self.store.try_transition(
                run.run_id,
                from_states={"running"},
                to_state="failed",
                result=_serialize_tool_result(ToolResult.failed(ToolError("tool execution timeout", code=ErrorCode.TIMEOUT))),
                metadata={"error": {"reason": "timeout"}},
            )
            self._record(context.session_id, {"event": "tool_run.failed", "tool_run_id": run.run_id, "tool_name": spec.name, "reason": "timeout"})
            return ToolResult.failed(ToolError("tool execution timeout", code=ErrorCode.TIMEOUT))

        # background：注册后台完成回调，再 CAS 转 reported_running。
        cf_future.add_done_callback(lambda fut: self._on_background_done(run.run_id, spec.name, context.session_id, fut))
        transitioned = self.store.try_transition(run.run_id, from_states={"running"}, to_state="reported_running")
        if not transitioned:
            # 回调已在 CAS 前抢先把运行推进到 completed_inline。
            current = self.store.get(run.run_id)
            if current.state == "completed_inline" and current.result is not None:
                return _tool_result_from_run_dict(current.result)
            return ToolResult.running(run_id=run.run_id, tool_name=spec.name, message=running_message)
        self._record(context.session_id, {"event": "tool_run.reported_running", "tool_run_id": run.run_id, "tool_name": spec.name})
        return ToolResult.running(run_id=run.run_id, tool_name=spec.name, message=running_message)

    def _on_background_done(self, run_id: str, tool_name: str, session_id: str, fut: Any) -> None:
        """后台 Tool 完成时推进 Tool Run 并触发 follow-up。

        主要逻辑：先尝试 `running->completed_inline`（覆盖回调早于窗口 CAS 的竞态），
        否则 `reported_running->completed_late/failed`；后者触发 follow-up 回调。
        参数：`fut` 为后台执行 future。
        返回值：无。
        异常情况：follow-up 回调异常被吞并记录，不影响后台线程。
        """

        if fut.cancelled():
            return
        error = fut.exception()
        timeout_reason = False
        if error is None:
            result = self._coerce_result(fut.result())
            outcome_ok = True
        elif isinstance(error, (asyncio.TimeoutError, TimeoutError)):
            result = ToolResult.failed(ToolError("tool background execution timeout", code=ErrorCode.TIMEOUT))
            outcome_ok = False
            timeout_reason = True
        else:
            result = ToolResult.failed(ToolError(str(error), code=ErrorCode.UNKNOWN))
            outcome_ok = False
        result_dict = _serialize_tool_result(result)
        if self.store.try_transition(run_id, from_states={"running"}, to_state="completed_inline", result=result_dict):
            self._record(session_id, {"event": "tool_run.completed_inline", "tool_run_id": run_id, "tool_name": tool_name, "ok": result.ok})
            return
        to_state = "completed_late" if outcome_ok else "failed"
        transition_metadata = {"error": {"reason": "timeout"}} if timeout_reason else None
        if not self.store.try_transition(run_id, from_states={"reported_running"}, to_state=to_state, result=result_dict, metadata=transition_metadata):
            self._record(session_id, {"event": "tool_run.duplicate_completion", "tool_run_id": run_id, "tool_name": tool_name})
            return
        self._record(session_id, {"event": f"tool_run.{to_state}", "tool_run_id": run_id, "tool_name": tool_name, "ok": result.ok})
        if self.on_background_complete is not None:
            try:
                run = self.store.get(run_id)
                self.on_background_complete(run, result)
            except Exception as exc:  # noqa: BLE001 - follow-up 回调异常不应影响后台线程
                self._record(session_id, {"event": "tool_run.follow_up.dispatch_failed", "tool_run_id": run_id, "tool_name": tool_name, "message": str(exc)})

    def cancel_run(self, run_id: str, *, reason: str = "user_requested") -> dict:
        """取消一个仍在后台运行的 Tool Run。

        主要逻辑：校验运行存在、声明了可取消、仍处于 running/reported_running；
        CAS 推进到 `cancelled` 后取消后台协程，让工具在 finally 清理端侧资源。
        参数：`run_id` 为目标运行；`reason` 为取消原因。
        返回值：结构化结果 `{ok, status, message}`。
        异常情况：无（错误经返回值表达）。
        """

        run = self.store.get_optional(run_id)
        if run is None:
            return {"ok": False, "status": "not_found", "message": "找不到该后台运行。"}
        if not bool((run.metadata or {}).get("cancel_supported")):
            return {"ok": False, "status": "not_cancellable", "message": "该能力不支持取消。"}
        if run.state not in {"running", "reported_running"}:
            return {"ok": False, "status": "too_late", "message": "该运行已经结束，来不及取消。"}
        transitioned = self.store.try_transition(
            run_id,
            from_states={"running", "reported_running"},
            to_state="cancelled",
            metadata={"cancel_reason": reason},
        )
        if not transitioned:
            return {"ok": False, "status": "too_late", "message": "该运行已经结束，来不及取消。"}
        self.runner.cancel(run_id)
        self._record(run.session_id, {"event": "tool_run.cancelled", "tool_run_id": run_id, "tool_name": run.tool_name, "reason": reason})
        return {"ok": True, "status": "cancelled", "message": "已取消。", "tool_run_id": run_id, "tool_name": run.tool_name}

    @staticmethod
    def _result_from_future(wrapped: Any) -> ToolResult:
        """从已完成的 future 读取 ToolResult，异常归一为失败结果。"""

        try:
            return ToolExecutor._coerce_result(wrapped.result())
        except RealtimeAgentError as exc:
            return ToolResult.failed(exc)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failed(ToolError(str(exc), code=ErrorCode.UNKNOWN))

    @staticmethod
    def _coerce_result(value: Any) -> ToolResult:
        """把 tool.run 返回值归一成 ToolResult。"""

        if isinstance(value, ToolResult):
            return value
        return ToolResult.failed(ToolError("tool returned non-ToolResult value", code=ErrorCode.PROTOCOL_ERROR))

    @staticmethod
    def _swallow_orphan(wrapped: Any) -> None:
        """避免被遗弃的 wrapper future 抛出 “exception never retrieved” 噪声。"""

        def _consume(fut: Any) -> None:
            if not fut.cancelled():
                fut.exception()

        wrapped.add_done_callback(_consume)

    def _record(self, session_id: str, payload: dict) -> None:
        """记录 Tool Run 生命周期事件。"""

        if self.recorder is not None and hasattr(self.recorder, "record_agent_event"):
            self.recorder.record_agent_event(session_id, payload)

    def _validate_input(self, tool: BaseTool, input_data: dict) -> dict:
        """按工具 input_model 校验并归一入参。

        主要逻辑：开发者使用 Pydantic BaseModel 声明入参时，SDK 在调用 Tool 前完成
        类型转换和必填校验；传给 run 的输入保持为 dict。
        参数：`tool` 为工具实例，`input_data` 为模型传入参数。
        返回值：校验后的 dict。
        异常情况：Pydantic 校验失败时抛出 `ValidationError`。
        """

        input_model = tool.resolved_spec().input_model
        if isinstance(input_model, dict) or input_model is dict or input_model is None:
            return dict(input_data or {})
        if inspect.isclass(input_model) and issubclass(input_model, BaseModel):
            model = input_model.model_validate(dict(input_data or {}))
            return model.model_dump(exclude_none=True)
        return dict(input_data or {})


def _background_timeout_for(tool: "BaseTool", spec: ToolSpec, *, wait_window: float, input_data: dict | None = None) -> float:
    """返回 background 工具的后台总超时。

    主要逻辑：工具可覆写 `background_timeout_seconds_for(input_data)` 按入参定预算
    （如计时器按秒数）；否则使用声明的 `background_timeout_seconds`；再否则使用默认
    后台超时。结果至少大于等待窗口，避免后台超时小于前台窗口造成立即过期。
    参数：`tool` 为工具实例；`spec` 为工具规格；`wait_window` 为等待窗口；`input_data` 为入参。
    返回值：后台总超时秒数。
    异常情况：无。
    """

    floor = float(wait_window) + 1.0
    resolver = getattr(tool, "background_timeout_seconds_for", None)
    if callable(resolver):
        try:
            resolved = resolver(dict(input_data or {}))
            if resolved is not None and float(resolved) > 0:
                return max(floor, float(resolved))
        except Exception:  # noqa: BLE001 - 解析失败回退到声明值
            pass
    declared = spec.background_timeout_seconds
    if declared is not None and float(declared) > 0:
        return float(declared)
    return max(floor, TOOL_RUN_BACKGROUND_TIMEOUT_SECONDS)


def _serialize_tool_result(result: ToolResult) -> dict[str, Any]:
    """把 ToolResult 序列化为可落盘和回流的稳定字典。

    主要逻辑：保留 ok/status/data/message/meta/error；assets 等富对象不进 Tool Run
    快照，follow-up 只需要文本与结构化数据驱动模型回复。
    参数：`result` 为工具结果。
    返回值：可 JSON 序列化的字典。
    异常情况：无。
    """

    return {
        "ok": bool(result.ok),
        "status": getattr(result, "status", "completed"),
        "data": result.data,
        "message": result.message,
        "meta": dict(result.meta or {}),
        "error": result.error,
    }


def _tool_result_from_run_dict(data: dict[str, Any]) -> ToolResult:
    """从 Tool Run 快照里的结果字典重建 ToolResult。"""

    return ToolResult(
        ok=bool(data.get("ok")),
        data=data.get("data"),
        message=str(data.get("message") or ""),
        assets=[],
        visual_assets=[],
        artifacts=[],
        tasks=[],
        meta=dict(data.get("meta") or {}),
        error=data.get("error"),
        status=str(data.get("status") or "completed"),
    )


class ToolGateway:
    """Agent Core 访问 Tool 的统一网关。

    主要功能：提供工具发现、schema 生成、策略校验、执行和 trace 记录入口。
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        policy: ToolPolicy | None = None,
        schema_builder: ToolSchemaBuilder | None = None,
        executor: ToolExecutor | None = None,
        context_factory: ToolContextFactory | None = None,
        recorder: Any = None,
        skill_service: Any = None,
        tool_run_store: ToolRunStore | None = None,
        tool_run_runner: ToolRunRunner | None = None,
        on_background_complete: Any = None,
        default_timeout_seconds: float = TOOL_DEFAULT_TIMEOUT_SECONDS,
        max_wait_timeout_seconds: float = TOOL_MAX_WAIT_TIMEOUT_SECONDS,
    ) -> None:
        self.default_timeout_seconds = float(default_timeout_seconds)
        self.max_wait_timeout_seconds = float(max_wait_timeout_seconds)
        self.registry = registry or ToolRegistry(
            default_timeout_seconds=self.default_timeout_seconds,
            max_wait_timeout_seconds=self.max_wait_timeout_seconds,
        )
        self.policy = policy or ToolPolicy()
        self.schema_builder = schema_builder or ToolSchemaBuilder(
            default_timeout_seconds=self.default_timeout_seconds,
            max_wait_timeout_seconds=self.max_wait_timeout_seconds,
        )
        self.tool_run_store = tool_run_store or ToolRunStore()
        self.tool_run_runner = tool_run_runner or ToolRunRunner()
        self.executor = executor or ToolExecutor(
            default_timeout_seconds=self.default_timeout_seconds,
            max_wait_timeout_seconds=self.max_wait_timeout_seconds,
            store=self.tool_run_store,
            runner=self.tool_run_runner,
            recorder=recorder,
            on_background_complete=on_background_complete,
        )
        # 注入的 executor 复用 gateway 的 store/recorder/回调，保证去重和可观测一致。
        if self.executor.recorder is None:
            self.executor.recorder = recorder
        if executor is not None:
            self.tool_run_store = self.executor.store
            self.tool_run_runner = self.executor.runner
            if self.executor.on_background_complete is None and on_background_complete is not None:
                self.executor.on_background_complete = on_background_complete
        self.context_factory = context_factory
        self.recorder = recorder
        self.skill_service = skill_service
        self._progress_emitted: set[tuple[str, str]] = set()
        # 把 Tool Run 管理门面交给上下文工厂，供 tool_run_manager 工具查询/取消后台运行。
        if context_factory is not None and hasattr(context_factory, "bind_tool_run_admin"):
            context_factory.bind_tool_run_admin(ToolRunAdmin(store=self.tool_run_store, executor=self.executor))

    def set_background_complete_handler(self, handler: Any) -> None:
        """注入后台 Tool Run 完成回调（由 FollowUpRouter 在装配阶段调用）。"""

        self.executor.on_background_complete = handler

    def list_tools(self) -> list[BaseTool]:
        """返回当前策略允许暴露给 Agent Core 的 Tool。"""

        return [
            tool
            for tool in self.registry.list_tools()
            if self.policy.allowed(tool.resolved_spec().name)
            and self._skill_allowed(tool.resolved_spec().name)
        ]

    def schemas(self) -> list[dict]:
        return [self.schema_builder.build(tool) for tool in self.list_tools()]

    def provider_schemas(self) -> list[dict]:
        """返回 provider function calling schema。"""

        return [self.schema_builder.build_provider_schema(tool) for tool in self.list_tools()]

    async def call(self, *, name: str, user_id: str, session_id: str, input_data: dict) -> ToolResult:
        started = time.time()
        trace_id = new_id("tool_trace")
        if not self.policy.allowed(name):
            result = ToolResult.failed(ToolError(f"tool is not allowed: {name}", code=ErrorCode.PERMISSION_DENIED))
            self._record_trace(trace_id, name, user_id, session_id, input_data, result, started)
            return result
        if self.context_factory is None:
            result = ToolResult.failed(ToolError("tool context factory is not configured", code=ErrorCode.PROTOCOL_ERROR))
            self._record_trace(trace_id, name, user_id, session_id, input_data, result, started)
            return result
        try:
            tool = self.registry.get(name)
        except ToolError as exc:
            result = ToolResult.failed(exc)
            self._record_trace(trace_id, name, user_id, session_id, input_data, result, started)
            return result
        result = await self.executor.execute(
            tool,
            self.context_factory.create(user_id=user_id, session_id=session_id, tool_name=name),
            input_data,
        )
        self._record_trace(trace_id, name, user_id, session_id, input_data, result, started)
        return result

    def emit_progress_once(
        self,
        *,
        name: str,
        user_id: str,
        session_id: str,
        output_service: Any = None,
        mode: str | None = None,
    ) -> bool:
        """在模型首输出为 Tool 调用时触发工具前置播报。

        主要逻辑：
        1. 读取 Tool 声明的 `progress_message` 或 `progress_messages`。
        2. 同一 session 内同一 Tool 只触发一次。
        3. 如果注入了 Output Service，同时下发 cached 或 realtime 提示音。

        参数：`name` 为工具名；`user_id/session_id` 定位当前轮次；`mode` 为提示音生成模式。
        返回值：实际触发时返回 True。
        异常情况：未知 Tool 或无提示文案时返回 False，不打断工具调用。
        """

        try:
            tool = self.registry.get(name)
        except ToolError:
            return False
        messages = self._progress_candidates(tool)
        if not messages:
            return False
        tool_name = tool.resolved_spec().name
        key = (session_id, tool_name)
        if key in self._progress_emitted:
            return False
        self._progress_emitted.add(key)
        message = messages[0]
        resolved_mode = str(
            mode
            or getattr(getattr(output_service, "router", None), "tool_progress_audio_mode", "")
            or "cached"
        )
        decision = None
        if output_service is not None and hasattr(output_service, "submit_tool_progress"):
            decision = output_service.submit_tool_progress(
                user_id=user_id,
                session_id=session_id,
                tool_name=tool_name,
                messages=messages,
                generation_mode=resolved_mode,
            )
        if self.recorder and hasattr(self.recorder, "record_agent_event"):
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "tool.progress_message.emitted",
                    "tool_name": tool_name,
                    "message": message,
                    "candidates": messages,
                    "generation_mode": resolved_mode,
                    "playback_decision": getattr(decision, "__dict__", decision),
                },
            )
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "context.notification.recorded",
                    "source_id": f"tool:{tool_name}:progress",
                    "channel": "output",
                    "event_name": "tool_progress",
                    "model_visible": False,
                    "message": message,
                },
            )
        return True

    def call_sync_safe(self, *, name: str, user_id: str, session_id: str, input_data: dict) -> ToolResult:
        """从同步 Agent 热路径安全调用异步 Tool。

        主要逻辑：
        1. 普通同步上下文中直接用 `asyncio.run()` 执行 `call()`。
        2. 如果当前线程已有事件循环，改在线程内创建独立事件循环执行，避免 aiohttp
           WebSocket 热路径触发 `asyncio.run() cannot be called from a running event loop`。

        参数：`name`、`user_id`、`session_id` 和 `input_data` 与 `call()` 保持一致。
        返回值：`ToolResult`。
        异常情况：worker 线程中出现的未捕获异常会重新抛给调用方。
        """

        coroutine = self.call(name=name, user_id=user_id, session_id=session_id, input_data=input_data)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)

        result: ToolResult | None = None
        error: BaseException | None = None

        def _worker() -> None:
            nonlocal result, error
            try:
                result = asyncio.run(coroutine)
            except BaseException as exc:  # noqa: BLE001 - 需要把 worker 异常带回主线程
                error = exc

        thread = threading.Thread(target=_worker, name=f"tool-call-{name}", daemon=True)
        thread.start()
        thread.join()
        if error is not None:
            raise error
        assert result is not None
        return result

    @staticmethod
    def _progress_candidates(tool: BaseTool) -> list[str]:
        """读取 Tool 的候选前置播报文案。"""

        return _progress_candidates(tool.resolved_spec().progress_message)

    def _record_trace(
        self,
        trace_id: str,
        name: str,
        user_id: str,
        session_id: str,
        input_data: dict,
        result: ToolResult,
        started: float,
    ) -> None:
        if not self.recorder or not hasattr(self.recorder, "record_tool_trace"):
            return
        trace = ToolTrace(
            trace_id=trace_id,
            tool_name=name,
            user_id=user_id,
            session_id=session_id,
            input_data=dict(input_data),
            ok=result.ok,
            duration_ms=int((time.time() - started) * 1000),
            result_message=result.message,
            error=result.error,
            created_at=started,
        )
        self.recorder.record_tool_trace(session_id, asdict(trace))

    def _skill_allowed(self, tool_name: str) -> bool:
        if self.skill_service is None:
            return True
        tool_allowlist = getattr(self.skill_service, "tool_allowlist", lambda: set())()
        return not tool_allowlist or tool_name in tool_allowlist


def _progress_candidates(progress_message: ProgressMessage | None) -> list[str]:
    """规范化工具前置播报候选文案。"""

    if isinstance(progress_message, str):
        raw = [progress_message]
    elif isinstance(progress_message, (tuple, list)):
        raw = [str(item) for item in progress_message]
    else:
        raw = []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = item.strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def _first_progress_message(progress_message: ProgressMessage | None) -> str | None:
    """返回首个前置播报文案。"""

    candidates = _progress_candidates(progress_message)
    return candidates[0] if candidates else None


@dataclass(frozen=True)
class DeviceSnapshot:
    """端侧设备的只读快照。

    主要功能：向 Tool / Task 暴露当前用户 active device set 的调试摘要。
    主要属性：`device_id` 只用于 debug 和只读展示，协议原生 Tool / Task API 不接受
    业务代码传入 device_id 做点对点投递。
    """

    device_id: str
    name: str = ""
    properties: dict = field(default_factory=dict)


class OutputStreamWriter:
    """协议原生 output stream 写入器。

    主要功能：让 Tool / Task 写入 `actuator.*` output stream，而不是直接操作端侧播放器。
    主要方法：`write()` 写入一片音频或触觉 payload，`close()` 请求关闭 output stream。
    主要属性：`stream_id`、`session_id` 标识服务端创建的 output stream。
    """

    def __init__(self, *, context: "DeviceRuntime", stream_id: str, session_id: str, stream_type: str, format: StreamFormat) -> None:
        self._context = context
        self.stream_id = stream_id
        self.session_id = session_id
        self.stream_type = stream_type
        self.format = format
        self._seq = 0

    def write(self, payload: bytes, *, final: bool = False, metadata: dict | None = None) -> None:
        """写入 output stream chunk。

        主要逻辑：构造 SDK `StreamChunk`，由 Stream Service 通过订阅分发到端侧。
        参数：`payload` 为二进制内容，`final` 表示是否最后一片，`metadata` 为附加诊断。
        返回值：无。
        异常情况：stream 已关闭或格式不匹配时由 Stream Service 抛出异常。
        """
        chunk = StreamChunk(
            user_id=self._context.user_id,
            session_id=self.session_id,
            stream_id=self.stream_id,
            stream_type=self.stream_type,
            seq=self._seq,
            payload=payload,
            codec=self.format.codec,
            sample_rate=self.format.sample_rate,
            channels=self.format.channels,
            duration_ms=self.format.chunk_ms,
            final=final,
            metadata=dict(metadata or {}),
        )
        self._context._app.stream_service.write_chunk(chunk)
        self._seq += 1
        if final:
            self.close(reason="final")

    def close(self, *, reason: str = "completed") -> None:
        """请求关闭当前 output stream。

        主要逻辑：调用 Stream Service 发布 output 完成或关闭请求。
        参数：`reason` 为关闭原因。
        返回值：无。
        异常情况：stream 不存在时由 Stream Service 抛出异常。
        """
        self._context._app.stream_service.close_stream(self.stream_id, reason=reason)


@dataclass(frozen=True)
class CommandResult:
    """设备命令执行结果。

    主要功能：作为 `context.devices.commands.call()` 的稳定返回结构，屏蔽底层
    控制事件和多设备投递细节。
    """

    command_id: str
    name: str
    ok: bool
    data: dict = field(default_factory=dict)
    device_count: int = 0
    errors: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class CommandEvent:
    """设备长命令事件。

    主要功能：描述 `CommandHandle.results()` 产生的远程命令状态。
    """

    command_id: str
    name: str
    state: str
    data: dict = field(default_factory=dict)
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass(frozen=True)
class DeviceLease:
    """设备能力选择结果。

    主要功能：作为 SDK 内部 selector 解析结果，封装 lease_id、设备快照、内部设备集合
    和默认参数，避免 typed API 后续步骤重新选择设备。
    """

    lease_id: str
    user_id: str
    capability: str | None
    selector: dict
    device_snapshot: tuple[DeviceSnapshot, ...]
    expires_at_ms: int
    devices: tuple[Any, ...]
    params: dict = field(default_factory=dict)


class CommandHandle:
    """设备长命令句柄。

    主要功能：给 Task 维护远程设备任务状态提供稳定对象，并在 `stop()` 时发送停止命令。
    """

    def __init__(self, *, context: "DeviceRuntime", command_id: str, name: str, selector: dict | None, params: dict | None) -> None:
        self._context = context
        self.command_id = command_id
        self.name = name
        self.selector = dict(selector or {})
        self.params = dict(params or {})
        self._stopped = False

    async def results(self) -> AsyncIterator[CommandEvent]:
        """异步读取命令状态。

        主要逻辑：只转发端侧 `command.*` 回执，直到 completed、failed 或 stop。
        """

        async for event in self._context._command_result_broker().subscribe(self.command_id):
            yield event
            if event.state in {"completed", "failed"}:
                break

    async def stop(self, *, reason: str = "task_cancelled") -> CommandResult:
        """停止远程命令。

        主要逻辑：发送 `*.stop` 约定命令，端侧可按 command_id 释放资源。
        """

        self._stopped = True
        return await _CommandsFacade(context=self._context, allow_long_running=True).call(
            name=f"{self.name}.stop",
            selector=self.selector,
            params={"command_id": self.command_id, "reason": reason},
        )


@dataclass(frozen=True)
class ActuatorResult:
    """执行器一次性动作结果。"""

    ok: bool
    delivered_count: int
    matched_count: int
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ActuatorStreamResult:
    """执行器流式动作结果。"""

    ok: bool
    stream_id: str | None = None
    frames_written: int = 0


class CommandResultBroker:
    """端侧命令回执分发器。

    主要功能：把 `command.accepted/progress/completed/failed`
    事件按 command_id 缓存并分发给等待中的 `commands.call()` 或
    `commands.subscribe_result()`。
    """

    TERMINAL_STATES = {"completed", "failed"}

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._history: dict[str, list[CommandEvent]] = {}
        self._subscribers: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[CommandEvent]]]] = {}
        self._command_devices: dict[str, set[str]] = {}
        self._device_commands: dict[str, set[str]] = {}
        self._terminal_devices: dict[str, set[str]] = {}
        self._command_metadata: dict[str, dict[str, Any]] = {}

    def register_command(
        self,
        *,
        command_id: str,
        name: str,
        device_ids: tuple[str, ...],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """登记一个已经下发、尚未结束的设备命令。

        主要逻辑：在 `command.requested` 下发前登记 command 与目标设备的关系。
        后续如果设备控制连接断开，SDK 可以把该设备上的未完成 command 转成
        `command.failed`，避免 Task 长时间等待已经失联的端侧。
        参数：`command_id/name/device_ids` 来自命令下发阶段。
        返回值：无。
        异常情况：无。
        """

        command_id = str(command_id or "").strip()
        if not command_id:
            return
        device_set = {str(device_id) for device_id in device_ids if str(device_id)}
        with self._lock:
            self._command_devices[command_id] = device_set
            self._command_metadata[command_id] = dict(metadata or {})
            self._terminal_devices.setdefault(command_id, set())
            for device_id in device_set:
                self._device_commands.setdefault(device_id, set()).add(command_id)

    def metadata_for(self, command_id: str) -> dict[str, Any]:
        """返回命令登记时保存的 task 等元数据。"""

        with self._lock:
            return dict(self._command_metadata.get(str(command_id or "").strip()) or {})

    def record(self, event: Event) -> None:
        """记录端侧命令回执并唤醒订阅者。"""

        payload = dict(event.payload or {})
        command_id = str(payload.get("command_id") or "").strip()
        if not command_id:
            return
        payload = {**self.metadata_for(command_id), **payload}
        state = str(event.event_name).rsplit(".", 1)[-1]
        command_event = CommandEvent(
            command_id=command_id,
            name=str(payload.get("command") or ""),
            state=state,
            data={"producer_id": event.producer_id, **payload},
        )
        self._append(command_event)
        if state in self.TERMINAL_STATES and event.producer_id:
            self._mark_terminal(command_id, str(event.producer_id))

    def fail_device_commands(self, *, device_id: str, reason: str = "device_offline") -> tuple[str, ...]:
        """把某台离线设备上的未完成命令标记为 failed。

        主要逻辑：控制连接断开或心跳超时时调用本方法。它只影响已登记且该设备尚未
        返回 completed/failed 的 command，不会重复覆盖已经终态的命令。
        参数：`device_id` 为离线设备，`reason` 为失败原因。
        返回值：被标记失败的 command_id 列表。
        异常情况：无。
        """

        device_id = str(device_id or "").strip()
        if not device_id:
            return tuple()
        failed: list[str] = []
        with self._lock:
            command_ids = list(self._device_commands.get(device_id, set()))
        for command_id in command_ids:
            with self._lock:
                if device_id in self._terminal_devices.get(command_id, set()):
                    continue
                history = self._history.get(command_id, [])
                name = history[-1].name if history else ""
            command_event = CommandEvent(
                command_id=command_id,
                name=name,
                state="failed",
                data={
                    "producer_id": device_id,
                    "command_id": command_id,
                    "command": name,
                    "message": f"device offline: {reason}",
                    "error_code": "device_offline",
                    "reason": reason,
                },
            )
            self._append(command_event)
            self._mark_terminal(command_id, device_id)
            failed.append(command_id)
        return tuple(failed)

    def _append(self, command_event: CommandEvent) -> None:
        """记录命令事件并通知订阅者。"""

        with self._lock:
            self._history.setdefault(command_event.command_id, []).append(command_event)
            subscribers = list(self._subscribers.get(command_event.command_id, []))
        stale: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[CommandEvent]]] = []
        for loop, queue in subscribers:
            if loop.is_closed():
                stale.append((loop, queue))
                continue
            try:
                loop.call_soon_threadsafe(queue.put_nowait, command_event)
            except RuntimeError:
                stale.append((loop, queue))
        if stale:
            with self._lock:
                current = self._subscribers.get(command_event.command_id, [])
                for item in stale:
                    if item in current:
                        current.remove(item)

    def _mark_terminal(self, command_id: str, device_id: str) -> None:
        """标记某台设备上的 command 已进入终态。"""

        with self._lock:
            terminal = self._terminal_devices.setdefault(command_id, set())
            terminal.add(device_id)
            if terminal >= self._command_devices.get(command_id, set()):
                for item in self._command_devices.pop(command_id, set()):
                    commands = self._device_commands.get(item)
                    if commands is not None:
                        commands.discard(command_id)
                        if not commands:
                            self._device_commands.pop(item, None)
                self._terminal_devices.pop(command_id, None)

    async def wait(
        self,
        command_id: str,
        *,
        expected_device_ids: tuple[str, ...],
        timeout_seconds: float,
    ) -> list[CommandEvent]:
        """等待命令终态回执。

        主要逻辑：按已投递设备集合聚合 completed/failed；超时时返回已经收到的回执，
        由调用方转换成结构化 CommandResult。
        """

        with self._lock:
            seen = list(self._history.get(command_id, []))
        if _command_terminal_device_ids(seen) >= set(expected_device_ids):
            return seen
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[CommandEvent] = asyncio.Queue()
        subscriber = (loop, queue)
        with self._lock:
            self._subscribers.setdefault(command_id, []).append(subscriber)
        deadline = time.time() + max(0.0, timeout_seconds)
        try:
            while _command_terminal_device_ids(seen) < set(expected_device_ids):
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                try:
                    seen.append(await asyncio.wait_for(queue.get(), timeout=remaining))
                except TimeoutError:
                    break
        finally:
            with self._lock:
                subscribers = self._subscribers.get(command_id, [])
                if subscriber in subscribers:
                    subscribers.remove(subscriber)
        return seen

    def subscribe(self, command_id: str) -> AsyncIterator[CommandEvent]:
        """订阅某个命令的真实端侧回执。"""

        async def _events() -> AsyncIterator[CommandEvent]:
            with self._lock:
                history = list(self._history.get(command_id, []))
            for event in history:
                yield event
                if event.state in self.TERMINAL_STATES:
                    return
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[CommandEvent] = asyncio.Queue()
            subscriber = (loop, queue)
            with self._lock:
                self._subscribers.setdefault(command_id, []).append(subscriber)
            try:
                while True:
                    event = await queue.get()
                    yield event
                    if event.state in self.TERMINAL_STATES:
                        break
            finally:
                with self._lock:
                    subscribers = self._subscribers.get(command_id, [])
                    if subscriber in subscribers:
                        subscribers.remove(subscriber)

        return _events()


def _command_terminal_device_ids(events: list[CommandEvent]) -> set[str]:
    """提取已经返回终态的设备 ID。"""

    return {
        str(event.data.get("producer_id") or "")
        for event in events
        if event.state in CommandResultBroker.TERMINAL_STATES and event.data.get("producer_id")
    }


def _command_task_metadata(params: dict | None) -> dict[str, Any]:
    """从命令参数中提取 Task 路由元数据。"""

    data = dict(params or {})
    task_id = str(data.get("task_id") or data.get("peer_session_id") or "").strip()
    task_type = str(data.get("task_type") or "").strip()
    metadata: dict[str, Any] = {}
    if task_id:
        metadata["task_id"] = task_id
    if task_type:
        metadata["task_type"] = task_type
    return metadata


class OutputFacade:
    """用户可感知输出门面。

    主要功能：让 Tool / Task 用 `context.output.say()` 表达播报意图，不直接写
    `actuator.speaker`。
    """

    def __init__(self, *, user_id: str, app) -> None:
        self.user_id = user_id
        self._app = app

    async def say(self, text: str, *, priority: str = "normal", ttl_seconds: int = 0, dedupe_key: str | None = None) -> None:
        """提交文本播报。

        参数：`text` 是要播报的文本；`priority` 和 `ttl_seconds` 交给 Output Service
        和播放仲裁处理；`dedupe_key` 预留给后续去重策略。
        """

        _ = dedupe_key
        session_id = self._app.active_session_id(self.user_id)
        self._app.output_service.submit_text(
            user_id=self.user_id,
            session_id=session_id,
            text=text,
            priority=priority,
            ttl_seconds=ttl_seconds,
        )

    async def close_audio_session(self, *, reason: str = "model_requested", close_mode: str = "close_now") -> None:
        """请求服务器关闭当前连续对话音频会话。"""

        self._app.close_audio_session(self.user_id, reason=reason, mode=close_mode)


class AssetFacade:
    """资产引用读取门面。

    主要功能：提供 `context.assets.get()`，让开发者按 asset_id 读取已生成的
    `AssetRef`。当前实现通过现有资产窗口扫描完成。
    """

    def __init__(self, *, user_id: str, app) -> None:
        self.user_id = user_id
        self._app = app

    def get(self, asset_id: str) -> AssetRef | None:
        """按 asset_id 查找资产引用。"""

        for stream_type in ("sensor.rgb", "sensor.imu", "sensor.tof"):
            for asset in self._app.asset_service.get_asset_window(user_id=self.user_id, stream_type=stream_type, limit=100):
                if asset.asset_id == asset_id:
                    return asset
        return None

    def claim_photo(self, *, asset_id: str, consumer: str, owner: str, reason: str = "") -> bool:
        """claim 一张 turn buffer 照片。

        主要逻辑：暴露给 Tool / Task 的轻量门面；返回 True 表示成功 claim，未知资产
        视为未进入自动消费 buffer，返回 False。
        参数：`asset_id` 为照片资产，`consumer/owner/reason` 描述消费方。
        返回值：是否成功 claim。
        异常情况：底层资产服务异常会向上抛出。
        """

        result = self._app.asset_service.claim_photo_asset(
            asset_id=asset_id,
            consumer=consumer,  # type: ignore[arg-type]
            owner=owner,
            reason=reason,
        )
        return result.ok


class _SensorCapability:
    """单个传感器 typed API 适配器。"""

    def __init__(self, *, context: "DeviceRuntime", stream_type: str, allow_stream: bool) -> None:
        self._context = context
        self.stream_type = stream_type
        self._allow_stream = allow_stream

    async def one(
        self,
        *,
        selector: dict | None = None,
        timeout_seconds: float = 10,
        params: dict | None = None,
    ) -> AssetRef:
        """请求一次传感器数据并返回 AssetRef。"""

        devices = self._context._resolve_devices_for_capability(self.stream_type, selector=selector, require_single=True)
        payload = {
            "mode": "single",
            **self._context._merge_capability_params(self.stream_type, devices=devices, params=params),
        }
        asset = self._context._request_asset_for_devices(
            stream_type=self.stream_type,
            devices=devices,
            freshness_seconds=0,
            params=payload,
            timeout_seconds=timeout_seconds,
        )
        if asset is None:
            raise StreamTimeoutError(
                f"sensor asset timeout: {self.stream_type}",
                code=ErrorCode.TIMEOUT,
                details={"stream_type": self.stream_type, "selector": selector or {}},
            )
        return asset

    def stream(
        self,
        *,
        selector: dict | None = None,
        fps: float | None = None,
        sample_rate_hz: float | None = None,
        duration_seconds: float | None = None,
        sample_count: int | None = None,
        params: dict | None = None,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[AssetRef]:
        """打开持续传感器流并逐个返回 AssetRef。"""

        if not self._allow_stream:
            raise RealtimeAgentError(
                f"streaming sensor API is only available to background tools: {self.stream_type}",
                code=ErrorCode.PERMISSION_DENIED,
            )
        return self._context._sensor_stream(
            stream_type=self.stream_type,
            selector=selector,
            fps=fps,
            sample_rate_hz=sample_rate_hz,
            duration_seconds=duration_seconds,
            sample_count=sample_count,
            params=params,
            timeout_seconds=timeout_seconds,
        )


class _SensorsFacade:
    """传感器 typed API 聚合对象。"""

    def __init__(self, *, context: "DeviceRuntime", allow_stream: bool) -> None:
        self.rgb = _SensorCapability(context=context, stream_type="sensor.rgb", allow_stream=allow_stream)
        self.imu = _SensorCapability(context=context, stream_type="sensor.imu", allow_stream=allow_stream)
        self.tof = _SensorCapability(context=context, stream_type="sensor.tof", allow_stream=allow_stream)


class _VibratorCapability:
    """振动器 typed API。"""

    def __init__(self, *, context: "DeviceRuntime", allow_stream: bool) -> None:
        self._context = context
        self._allow_stream = allow_stream

    async def one(
        self,
        *,
        selector: dict | None = None,
        params: dict | None = None,
        timeout_seconds: float = 5,
    ) -> ActuatorResult:
        """执行一次振动动作。"""

        _ = timeout_seconds
        devices = self._context._resolve_devices_for_capability("actuator.haptic", selector=selector, require_single=False)
        payload = {
            "command": "haptic.vibrate",
            **self._context._merge_capability_params("actuator.haptic", devices=devices, params=params),
        }
        result = self._context._publish_event_to_devices(
            event_name=EventName.COMMAND_REQUESTED,
            payload=payload,
            stream_type="actuator.haptic",
            devices=devices,
        )
        return ActuatorResult(
            ok=result.delivered_count > 0,
            delivered_count=result.delivered_count,
            matched_count=len(devices),
            data={"command": "haptic.vibrate"},
        )

    async def stream(
        self,
        *,
        selector: dict | None = None,
        frames,
        params: dict | None = None,
    ) -> ActuatorStreamResult:
        """发送振动器连续数据。"""

        if not self._allow_stream:
            raise RealtimeAgentError("streaming actuator API is only available to background tools", code=ErrorCode.PERMISSION_DENIED)
        devices = self._context._resolve_devices_for_capability("actuator.haptic", selector=selector, require_single=True)
        merged_params = self._context._merge_capability_params("actuator.haptic", devices=devices, params=params)
        writer = self._context._open_output_stream_for_devices(
            "actuator.haptic",
            codec=str(merged_params.get("codec") or "raw"),
            devices=devices,
        )
        count = 0
        for frame in frames:
            writer.write(bytes(frame), final=False)
            count += 1
        writer.write(b"", final=True)
        return ActuatorStreamResult(ok=True, stream_id=writer.stream_id, frames_written=count)


class _ActuatorsFacade:
    """执行器 typed API 聚合对象。"""

    def __init__(self, *, context: "DeviceRuntime", allow_stream: bool) -> None:
        self.vibrator = _VibratorCapability(context=context, allow_stream=allow_stream)


class _CommandsFacade:
    """设备命令 typed API。"""

    def __init__(self, *, context: "DeviceRuntime", allow_long_running: bool) -> None:
        self._context = context
        self._allow_long_running = allow_long_running

    async def call(
        self,
        *,
        name: str,
        selector: dict | None = None,
        params: dict | None = None,
        timeout_seconds: float = 5,
        require_single: bool = False,
    ) -> CommandResult:
        """执行一次设备命令。"""

        devices = self._context._resolve_devices_for_command(selector=selector, require_single=require_single)
        command_id = new_id("cmd")
        broker = self._context._command_result_broker()
        broker.register_command(
            command_id=command_id,
            name=name,
            device_ids=tuple(str(device.device_id) for device in devices),
            metadata=_command_task_metadata(params),
        )
        result = self._context._publish_event_to_devices(
            event_name=EventName.COMMAND_REQUESTED,
            payload={"command_id": command_id, "command": name, "params": dict(params or {})},
            devices=devices,
        )
        self._context._record_command_trace(
            {
                "event": "command.call.requested",
                "command_id": command_id,
                "command": name,
                "selector": dict(selector or {}),
                "device_count": len(devices),
                "delivered_count": result.delivered_count,
            }
        )
        expected_device_ids = tuple(device_id for device_id in result.matched_device_ids if device_id not in result.failed_device_ids)
        events = await broker.wait(
            command_id,
            expected_device_ids=expected_device_ids,
            timeout_seconds=timeout_seconds,
        )
        failed_events = [event for event in events if event.state == "failed"]
        completed_events = [event for event in events if event.state == "completed"]
        terminal_device_ids = _command_terminal_device_ids(events)
        timeout_device_ids = sorted(set(expected_device_ids) - terminal_device_ids)
        errors = [{"device_id": device_id, "message": "delivery_failed"} for device_id in result.failed_device_ids]
        errors.extend(
            {
                "device_id": str(event.data.get("producer_id") or ""),
                "message": str(event.data.get("message") or event.data.get("error") or "command_failed"),
                "data": dict(event.data),
            }
            for event in failed_events
        )
        errors.extend({"device_id": device_id, "message": "command_result_timeout"} for device_id in timeout_device_ids)
        command_result = CommandResult(
            command_id=command_id,
            name=name,
            ok=result.delivered_count > 0 and not errors and len(completed_events) >= len(expected_device_ids),
            data={
                "params": dict(params or {}),
                "events": [event.__dict__ for event in events],
                "completed_count": len(completed_events),
            },
            device_count=len(devices),
            errors=errors,
        )
        self._context._record_command_trace(
            {
                "event": "command.call.completed",
                "command_id": command_id,
                "command": name,
                "ok": command_result.ok,
                "device_count": len(devices),
                "completed_count": len(completed_events),
                "error_count": len(errors),
                "errors": errors,
            }
        )
        return command_result

    async def start(
        self,
        *,
        name: str,
        selector: dict | None = None,
        params: dict | None = None,
    ) -> CommandHandle:
        """启动远程长命令。"""

        if not self._allow_long_running:
            raise RealtimeAgentError("long running commands are only available to background tools", code=ErrorCode.PERMISSION_DENIED)
        devices = self._context._resolve_devices_for_command(selector=selector, require_single=True)
        command_id = new_id("cmd")
        broker = self._context._command_result_broker()
        broker.register_command(
            command_id=command_id,
            name=name,
            device_ids=tuple(str(device.device_id) for device in devices),
            metadata=_command_task_metadata(params),
        )
        self._context._publish_event_to_devices(
            event_name=EventName.COMMAND_REQUESTED,
            payload={"command_id": command_id, "command": name, "mode": "start", "params": dict(params or {})},
            devices=devices,
        )
        self._context._record_command_trace(
            {
                "event": "command.start.requested",
                "command_id": command_id,
                "command": name,
                "selector": dict(selector or {}),
                "device_count": len(devices),
            }
        )
        return CommandHandle(context=self._context, command_id=command_id, name=name, selector=selector, params=params)

    async def stop(self, command_id: str, *, name: str = "device.command", reason: str = "requested") -> CommandResult:
        """停止远程长命令。"""

        return await self.call(name=f"{name}.stop", params={"command_id": command_id, "reason": reason})

    def subscribe_result(self, command_id: str) -> AsyncIterator[CommandEvent]:
        """订阅远程命令结果。"""

        return self._context._command_result_broker().subscribe(command_id)


class ToolDeviceFacade:
    """Tool 可见设备能力门面。

    主要功能：只暴露短生命周期 typed device API，避免业务 Tool 访问底层控制信令、
    stream 监听或内部设备对象。
    """

    def __init__(self, *, context: "DeviceRuntime") -> None:
        self._context = context
        self.sensors = _SensorsFacade(context=context, allow_stream=False)
        self.actuators = _ActuatorsFacade(context=context, allow_stream=False)
        self.commands = _CommandsFacade(context=context, allow_long_running=False)

    def _get_devices(self) -> list[DeviceSnapshot]:
        """供 SDK 内置系统工具读取设备快照，普通业务代码不应依赖。"""

        return self._context._device_snapshots()

class BackgroundDeviceFacade(ToolDeviceFacade):
    """background 工具可见设备能力门面。

    主要功能：在 ToolDeviceFacade 的短生命周期能力上，额外开放持续 stream 和远程
    长命令能力；由 ToolContextFactory 在 `late_result_policy=background` 时注入。
    """

    def __init__(self, *, context: "DeviceRuntime") -> None:
        self._context = context
        self.sensors = _SensorsFacade(context=context, allow_stream=True)
        self.actuators = _ActuatorsFacade(context=context, allow_stream=True)
        self.commands = _CommandsFacade(context=context, allow_long_running=True)


def _coerce_tags(value: Any) -> list[str]:
    """把注册字段里的标签统一成字符串列表。"""

    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def _selector_value(device: Any, properties: dict, key: str) -> Any:
    """读取 selector 可匹配字段。"""

    if hasattr(device, key):
        return getattr(device, key)
    if key in properties:
        return properties[key]
    cursor: Any = properties
    for part in key.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            cursor = None
            break
        cursor = cursor[part]
    if cursor is not None:
        return cursor
    dotted_key = key.replace(".", "_")
    if hasattr(device, dotted_key):
        return getattr(device, dotted_key)
    return properties.get(dotted_key)


FORBIDDEN_SELECTOR_FIELDS = {
    "device_id",
    "target_device",
    "target_device_id",
    "source_device",
    "source_device_id",
}


def _device_support_defaults(device: Any, capability: str | None) -> dict:
    """读取设备注册声明中的能力默认参数。"""

    if capability is None:
        return {}
    properties = dict(getattr(device, "properties", {}) or {})
    defaults = properties.get("realtime_agent.support_defaults") or {}
    if not isinstance(defaults, dict):
        return {}
    value = defaults.get(str(capability)) or {}
    return dict(value) if isinstance(value, dict) else {}


class DeviceRuntime:
    """SDK 内部设备运行时上下文。

    主要功能：承接 typed facade 的设备解析、事件投递、stream 打开和资产查询；
    不作为公开 API 导出，业务代码只能看到 `ToolDeviceFacade` 或 `BackgroundDeviceFacade`。
    """

    def __init__(self, *, user_id: str, app, allow_long_running: bool = False) -> None:
        self.user_id = user_id
        self._app = app
        self._allow_long_running = allow_long_running

    def _device_snapshots(self) -> list[DeviceSnapshot]:
        """返回当前用户 active device set 的只读快照。

        主要逻辑：从 Control Service 读取在线设备，返回身份、名称、properties 和
        设备属性摘要，供 Tool / Task 做状态说明或调试展示。通讯仍然必须走事件和 stream。
        参数：无。
        返回值：`DeviceSnapshot` 列表。
        异常情况：无在线设备时返回空列表。
        """
        devices = []
        for record in self._app.control_service.get_active_device_set(self.user_id).devices:
            devices.append(
                DeviceSnapshot(
                    device_id=record.device_id,
                    name=getattr(record, "name", getattr(record, "device_name", "")),
                    properties=dict(getattr(record, "properties", {}) or {}),
                )
            )
        return devices

    def _open_output_stream_for_devices(
        self,
        stream_type: str,
        *,
        codec: str,
        devices: list[Any],
    ) -> OutputStreamWriter:
        """向已冻结的设备集合打开 output stream。"""

        session_id = str(devices[0].device_id) if len(devices) == 1 else self._app.active_session_id(self.user_id)
        format = StreamFormat(codec=codec)
        handle = self._app.stream_service.open_stream(
            user_id=self.user_id,
            session_id=session_id,
            stream_type=stream_type,
            producer_id=SERVER_PRODUCER_ID,
            format=format,
            stream_id=new_id("stream_out"),
            consumer_device_ids=tuple(str(device.device_id) for device in devices),
        )
        return OutputStreamWriter(context=self, stream_id=handle.stream_id, session_id=session_id, stream_type=stream_type, format=format)

    def _command_result_broker(self) -> CommandResultBroker:
        """获取应用级命令回执 broker。"""

        broker = getattr(self._app, "_command_result_broker", None)
        if broker is None:
            broker = CommandResultBroker()
            setattr(self._app, "_command_result_broker", broker)
        return broker

    def _request_asset_for_devices(
        self,
        *,
        stream_type: str | StreamType,
        devices: list[Any],
        freshness_seconds: float = 0,
        params: dict | None = None,
        timeout_seconds: float | None = None,
    ) -> AssetRef | None:
        """向已按 selector 冻结的设备集合请求单个资产。

        主要逻辑：typed facade 先解析设备，再把内部设备集合交给 Asset Service；
        业务代码仍然只使用 selector，不接触 device_id。
        """

        return self._app.asset_service.request_asset(
            user_id=self.user_id,
            stream_type=str(stream_type),
            freshness_seconds=freshness_seconds,
            params=params,
            session_id=self._app.active_session_id(self.user_id),
            timeout_seconds=timeout_seconds,
            device_ids=tuple(str(device.device_id) for device in devices),
        )

    def _publish_event_to_devices(
        self,
        *,
        event_name: str | EventName,
        payload: dict | None,
        devices: list[Any],
        stream_type: str | StreamType | None = None,
    ) -> PublishResult:
        """向 typed facade 已经解析出的内部设备集合投递控制事件。

        主要逻辑：这是 SDK 内部 helper，用于保证 selector 真实影响投递结果；
        对外仍不提供按 device_id 发送事件的 API。
        """

        event = Event(
            event_name=event_name,
            user_id=self.user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=str(devices[0].device_id) if len(devices) == 1 else self._app.active_session_id(self.user_id),
            stream_type=stream_type,
            payload=dict(payload or {}),
        )
        return self._app.control_service._push_event_to_device_ids(
            event,
            tuple(str(device.device_id) for device in devices),
        )

    async def _sensor_stream(
        self,
        *,
        stream_type: str,
        selector: dict | None = None,
        fps: float | None = None,
        sample_rate_hz: float | None = None,
        duration_seconds: float | None = None,
        sample_count: int | None = None,
        params: dict | None = None,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[AssetRef]:
        """typed sensor stream 的内部实现。

        主要逻辑：先按 selector 确认只命中一台设备，再复用当前协议原生
        `stream.control.open.requested` 和内部资产订阅。
        """

        devices = self._resolve_devices_for_capability(stream_type, selector=selector, require_single=True)
        correlation_id = new_id("stream_req")
        payload = self._merge_capability_params(stream_type, devices=devices, params=params)
        payload["correlation_id"] = correlation_id
        payload.setdefault("asset_policy", "cache")
        if fps is not None:
            payload["fps"] = fps
        if sample_rate_hz is not None:
            payload["sample_rate_hz"] = sample_rate_hz
        if sample_count is not None:
            payload["sample_count"] = sample_count
        open_payload = {
            "stream_type": str(stream_type),
            "mode": "continuous",
            "rate_hz": fps or sample_rate_hz,
            "duration_seconds": duration_seconds,
            **payload,
        }
        self._publish_event_to_devices(
            event_name="stream.control.open.requested",
            stream_type=stream_type,
            payload=open_payload,
            devices=devices,
        )
        count = 0
        try:
            async for asset in self._watch_assets_filtered(
                stream_type=stream_type,
                correlation_id=correlation_id,
                timeout_seconds=timeout_seconds if timeout_seconds is not None else duration_seconds,
                since=None,
            ):
                yield asset
                count += 1
                if sample_count is not None and count >= sample_count:
                    break
        finally:
            close_payload = {"stream_type": str(stream_type), "mode": "stop", "reason": "typed_stream_closed", "correlation_id": correlation_id}
            self._publish_event_to_devices(
                event_name="stream.control.close.requested",
                stream_type=stream_type,
                payload=close_payload,
                devices=devices,
            )

    def _resolve_devices_for_command(self, *, selector: dict | None, require_single: bool) -> list[Any]:
        return self._resolve_devices_for_capability(None, selector=selector, require_single=require_single)

    def _resolve_devices_for_capability(
        self,
        capability: str | None,
        *,
        selector: dict | None,
        require_single: bool,
    ) -> list[Any]:
        """按能力和 selector 解析在线设备。

        主要逻辑：复用 Control Service 的订阅匹配能力，再在 SDK 内部应用 selector。
        当前实现只返回设备对象，不把 device_id 暴露给业务 API。
        """

        started_at = time.time()
        if capability and str(capability).startswith("sensor."):
            event_name = "stream.control.open.requested"
            payload = {"stream_type": capability}
        elif capability == "actuator.haptic":
            event_name = EventName.COMMAND_REQUESTED
            payload = {"command": "haptic.vibrate"}
        else:
            event_name = EventName.COMMAND_REQUESTED
            payload = {"command": "*"}
        event = Event(
            event_name=event_name,
            user_id=self.user_id,
            producer_id=SERVER_PRODUCER_ID,
            stream_type=capability,
            payload=payload,
        )
        try:
            candidates = self._app.control_service.resolve_matching_devices(event, selection="all")
        except Exception:
            candidates = list(self._app.control_service.get_active_device_set(self.user_id).devices)
        if not candidates and capability is None:
            candidates = list(self._app.control_service.get_active_device_set(self.user_id).devices)
        active_devices = list(self._app.control_service.get_active_device_set(self.user_id).devices)
        active_summary = [
            {
                "device_id": str(getattr(device, "device_id", "")),
                "name": str(getattr(device, "name", getattr(device, "device_name", ""))),
                "properties": dict(getattr(device, "properties", {}) or {}),
            }
            for device in active_devices
        ]
        if capability is not None and not candidates and active_devices:
            raise CapabilityNotSupportedError(
                f"no online device supports capability: {capability}",
                code=ErrorCode.NOT_FOUND,
                details={"capability": capability, "selector": selector or {}, "active_devices": active_summary},
            )
        devices = [device for device in candidates if self._selector_matches(device, selector or {})]
        if not devices:
            raise DeviceNotFoundError(
                f"no online device matches capability: {capability or 'command'}",
                code=ErrorCode.NOT_FOUND,
                details={"capability": capability, "selector": selector or {}, "active_devices": active_summary},
            )
        if require_single and len(devices) > 1:
            raise AmbiguousDeviceError(
                f"multiple devices match capability: {capability or 'command'}",
                code=ErrorCode.INVALID_ARGUMENT,
                details={"capability": capability, "selector": selector or {}, "matched_count": len(devices)},
            )
        snapshots = tuple(
            DeviceSnapshot(
                device_id=device.device_id,
                name=getattr(device, "name", getattr(device, "device_name", "")),
                properties=dict(getattr(device, "properties", {}) or {}),
            )
            for device in devices
        )
        lease = DeviceLease(
            lease_id=new_id("lease"),
            user_id=self.user_id,
            capability=capability,
            selector=dict(selector or {}),
            device_snapshot=snapshots,
            expires_at_ms=int((time.time() + 30) * 1000),
            devices=tuple(devices),
            params=_device_support_defaults(devices[0], capability) if devices else {},
        )
        self._record_capability_trace(
            {
                "event": "capability.resolved",
                "capability": capability or "command",
                "selector": lease.selector,
                "matched_count": len(lease.devices),
                "require_single": require_single,
                "lease_id": lease.lease_id,
                "duration_ms": int((time.time() - started_at) * 1000),
                "result": "ok",
            }
        )
        return list(lease.devices)

    def _selector_matches(self, device: Any, selector: dict) -> bool:
        """检查设备是否匹配 selector。"""

        if not selector:
            return True
        properties = dict(getattr(device, "properties", {}) or {})
        tags = _coerce_tags(getattr(device, "tags", None) or properties.get("tags") or properties.get("device.tags"))
        for key, expected in selector.items():
            if key in FORBIDDEN_SELECTOR_FIELDS:
                raise RealtimeAgentError(
                    f"selector must not use {key}",
                    code=ErrorCode.INVALID_ARGUMENT,
                    details={"field": key},
                )
            if key == "capability":
                support_ids = properties.get("realtime_agent.support_ids") or []
                if expected not in support_ids:
                    return False
                continue
            if key == "tags":
                expected_tags = _coerce_tags(expected)
                if not set(expected_tags).issubset(set(tags)):
                    return False
                continue
            actual = _selector_value(device, properties, key)
            if actual != expected:
                return False
        return True

    def _merge_capability_params(self, capability: str, *, devices: list[Any], params: dict | None) -> dict:
        """合并设备默认参数和本次调用参数。"""

        defaults = _device_support_defaults(devices[0], capability) if devices else {}
        return {**defaults, **dict(params or {})}

    def _record_capability_trace(self, record: dict[str, Any]) -> None:
        """记录能力解析轨迹。"""

        recorder = getattr(self._app, "recorder", None)
        if recorder is None:
            return
        payload = {"component": "capability_api", "severity": "info", "user_id": self.user_id, **record}
        session_id = self._app.active_session_id(self.user_id)
        if session_id and hasattr(recorder, "record_capability_trace"):
            recorder.record_capability_trace(session_id, payload)
        elif hasattr(recorder, "record_system_event"):
            recorder.record_system_event(payload)

    def _record_command_trace(self, record: dict[str, Any]) -> None:
        """记录命令 API 调用轨迹。"""

        recorder = getattr(self._app, "recorder", None)
        if recorder is None:
            return
        payload = {"component": "command_api", "severity": "info", "user_id": self.user_id, **record}
        session_id = self._app.active_session_id(self.user_id)
        if session_id and hasattr(recorder, "record_command_trace"):
            recorder.record_command_trace(session_id, payload)
        elif hasattr(recorder, "record_system_event"):
            recorder.record_system_event(payload)

    async def _watch_assets_filtered(
        self,
        *,
        stream_type: str | StreamType,
        correlation_id: str | None,
        timeout_seconds: float | None,
        since: float | str | None,
    ) -> AsyncIterator[AssetRef]:
        """按 since 过滤 Asset Service 返回的资产迭代器。"""

        async for ref in self._app.asset_service.watch_assets(
            user_id=self.user_id,
            stream_type=stream_type,
            correlation_id=correlation_id,
            timeout_seconds=timeout_seconds,
        ):
            if since is None:
                yield ref
                continue
            if isinstance(since, (int, float)) and ref.created_at_ms <= int(float(since) * 1000):
                continue
            if isinstance(since, str) and ref.asset_id <= since:
                continue
            yield ref


class QueryDeviceStateTool(BaseTool):
    """查询当前用户 active device set 的内置 Tool。"""

    class Input(BaseModel):
        include_properties: bool = Field(default=True, description="是否返回设备公开 properties 摘要。")

    class Output(BaseModel):
        devices: list[dict] = Field(description="当前用户在线设备快照列表。")
        count: int = Field(description="匹配设备数量。")

    spec = ToolSpec(
        name="query_device_state",
        description="查询当前用户有哪些设备在线，以及设备名称、能力、连接状态或播放状态。",
        input_model=Input,
        output_model=Output,
        capability_type="tool",
        tags=["device", "debug", "system"],
        progress_message=(
            "我查一下当前设备状态。",
            "稍等，我看一下有哪些设备在线。",
        ),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        devices = context.devices._get_devices()
        include_properties = bool(input_data.get("include_properties", True))
        rows = []
        for device in devices:
            row = dict(device.__dict__)
            if not include_properties:
                row.pop("properties", None)
            rows.append(row)
        data = {"devices": rows, "count": len(devices)}
        return ToolResult.success(
            data=data,
            message=f"{len(devices)} active devices online",
        )


class ToolRunAdmin:
    """Tool Run 管理门面。

    主要功能：给内置 `tool_run_manager` 工具提供查询、列出、取消后台 Tool Run 的稳定入口，
    封装 ToolRunStore 与 ToolExecutor.cancel_run，避免 Tool 直接操作内部存储。
    """

    def __init__(self, *, store: Any, executor: "ToolExecutor") -> None:
        self._store = store
        self._executor = executor

    def list_instances(self, *, user_id: str, include_terminal: bool = True) -> list[dict[str, Any]]:
        """列出某用户的后台运行快照。"""

        runs = [run for run in self._store.list_runs() if run.user_id == user_id]
        if not include_terminal:
            runs = [run for run in runs if not run.is_terminal]
        runs.sort(key=lambda run: run.created_at)
        return [self._public(run) for run in runs]

    def query(self, run_id: str) -> dict[str, Any] | None:
        """查询单个后台运行快照。"""

        run = self._store.get_optional(run_id)
        return self._public(run) if run is not None else None

    def cancel(self, run_id: str, *, reason: str = "user_requested") -> dict[str, Any]:
        """取消一个后台运行。"""

        return self._executor.cancel_run(run_id, reason=reason)

    @staticmethod
    def _public(run: Any) -> dict[str, Any]:
        """把 ToolRun 转成模型/调试可读的公开视图。"""

        return {
            "tool_run_id": run.run_id,
            "tool_name": run.tool_name,
            "state": run.state,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "cancel_supported": bool((run.metadata or {}).get("cancel_supported")),
        }


class ToolRunManagerTool(BaseTool):
    """统一管理后台 Tool Run 的内置 Tool。

    主要功能：以一个模型可见工具承载后台运行的查询、列出和取消能力。
    启动一次能力直接调用对应业务工具即可；超窗会返回带 tool_run_id 的“运行中”结果。
    """

    class Input(BaseModel):
        action: Literal["query", "cancel", "list_instances"] = Field(
            description="后台运行管理动作：query 查询单个运行，cancel 取消运行，list_instances 列出运行。",
        )
        tool_run_id: str | None = Field(default=None, description="查询或取消时使用的后台运行编号（工具返回“运行中”结果时携带）。")
        include_terminal: bool = Field(default=True, description="列出运行时是否包含已完成、已取消、失败的运行。")

    spec = ToolSpec(
        name="tool_run_manager",
        description=(
            "统一管理后台运行的能力（Tool Run）。只用于查询、取消或列出后台运行；"
            "启动能力请直接调用对应工具。取消请提供工具返回的 tool_run_id。"
        ),
        input_model=Input,
        capability_type="tool",
        tags=["tool_run", "manage", "system"],
        progress_message=(
            "我处理一下这个后台任务。",
            "稍等，我看一下后台运行。",
        ),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行后台运行管理动作。

        参数：`action` 指定管理动作；`tool_run_id` 是查询/取消所需运行编号。
        返回值：成功时返回运行快照或列表；取消返回取消结果。
        异常情况：管理门面未配置、缺少必要参数或运行不存在时返回结构化失败。
        """

        if not isinstance(context, SystemToolContext) or context.tool_runs is None:
            return ToolResult.failed(ToolError("tool run admin is not configured", code=ErrorCode.PROTOCOL_ERROR))
        admin = context.tool_runs
        action = str(input_data.get("action") or "").strip()
        if action == "list_instances":
            rows = admin.list_instances(
                user_id=context.user_id,
                include_terminal=bool(input_data.get("include_terminal", True)),
            )
            return ToolResult.success(data={"tool_runs": rows}, message=f"{len(rows)} tool runs listed")
        if action == "query":
            run_id = str(input_data.get("tool_run_id") or "").strip()
            if not run_id:
                return ToolResult.failed(ToolError("tool_run_id is required", code=ErrorCode.INVALID_ARGUMENT))
            row = admin.query(run_id)
            if row is None:
                return ToolResult.failed(ToolError("tool run not found", code=ErrorCode.NOT_FOUND))
            return ToolResult.success(data=row, message=str(row.get("state") or ""))
        if action == "cancel":
            run_id = str(input_data.get("tool_run_id") or "").strip()
            if not run_id:
                return ToolResult.failed(ToolError("tool_run_id is required", code=ErrorCode.INVALID_ARGUMENT))
            outcome = admin.cancel(run_id, reason="tool_requested")
            return ToolResult.success(data=outcome, message=str(outcome.get("message") or ""))
        return ToolResult.failed(ToolError(f"unknown tool run action: {action}", code=ErrorCode.INVALID_ARGUMENT))


class TimerInput(BaseModel):
    """计时器工具启动参数。"""

    seconds: int = Field(
        ge=0,
        description="计时时长，单位秒。模型必须把分钟、小时换算成秒；普通计时应大于 0。",
    )
    message: str = Field(
        default="",
        description="到点时播报给用户的话；用户没有指定内容时可以留空。",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, data: Any) -> Any:
        """兼容 provider 常见别名：duration/delay/timeout_seconds、notify_text/text。"""

        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if normalized.get("seconds") is None:
            for alias in ("duration_seconds", "delay_seconds", "timeout_seconds", "duration"):
                if normalized.get(alias) is not None:
                    normalized["seconds"] = int(float(normalized[alias]))
                    break
        message = str(normalized.get("message") or normalized.get("notify_text") or normalized.get("text") or "").strip()
        if message:
            normalized["message"] = message
        return normalized


class TimerTool(BaseTool):
    """计时器工具。

    主要功能：用户要求倒计时、计时、稍后提醒或到点提示时启动；工具在后台等待指定
    秒数后，把到点提醒经 Output Service 直通播报给用户。

    与旧 timer_task 的等价关系：超过等待窗口（默认 3 秒）的计时会立即返回“计时器已开始
    计时”，到点后通过 late result direct 通道播报；可经 tool_run_manager 取消。
    """

    spec = ToolSpec(
        name="start_timer",
        description=(
            "启动计时器。用于用户要求倒计时、计时、稍后提醒或到点提示；"
            "工具会立即告知已开始计时，并在指定秒数后播报到点提醒。"
            "取消计时请使用 tool_run_manager。"
        ),
        input_model=TimerInput,
        capability_type="tool",
        tags=["timer", "reminder", "background"],
        late_result_policy="background",
        late_result_notify="direct",
        cancel_supported=True,
        follow_up_ttl_seconds=0,
        running_message="计时器已开始计时。",
    )

    def background_timeout_seconds_for(self, input_data: dict) -> float:
        """按计时秒数定后台总超时预算（留出余量避免到点前被强制取消）。"""

        seconds = max(0, int(float(input_data.get("seconds") or 0)))
        return float(seconds) + 30.0

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """等待指定秒数后返回到点提醒。

        主要逻辑：后台 runner 上 `asyncio.sleep(seconds)`，到点返回到点文案；
        该结果经 late result direct 通道直通播报。取消时协程在 sleep 处收到
        CancelledError，运行进入 cancelled，不再播报。
        参数：`context` 为 SDK 注入上下文；`input_data` 含 seconds/message。
        返回值：到点提醒 `ToolResult`。
        异常情况：取消时抛出 CancelledError，由 SDK 标记 cancelled。
        """

        seconds = max(0, int(float(input_data.get("seconds") or 0)))
        message = str(input_data.get("message") or "").strip() or (f"{seconds} 秒计时器到点了。" if seconds > 0 else "计时器到点了。")
        if seconds > 0:
            await asyncio.sleep(seconds)
        return ToolResult.success(data={"seconds": seconds, "notified": True}, message=message)


class CloseAudioSessionTool(BaseTool):
    """关闭当前连续对话音频会话的内置 Tool。"""

    class Input(BaseModel):
        reason: str = Field(default="model_requested", description="关闭连续对话的原因。")
        user_close_phrase: str = Field(
            default="",
            description=(
                "用户原话中明确要求结束本次连续语音会话的短语，例如“结束对话”、"
                "“退出语音会话”、“关闭连接”。普通插话、换话题、取消上一句或“算了”不能填写。"
            ),
        )

    spec = ToolSpec(
        name="close_audio_session",
        description=(
            "仅当用户明确要求结束本次连续语音会话、退出语音会话或关闭连接时调用。"
            "不要因为用户插话、取消上一句、说“算了”、换话题、暂停某个回答或表达否定而调用。"
            "调用时必须在 user_close_phrase 中填入用户原话里的明确关闭短语。"
        ),
        input_model=Input,
        capability_type="tool",
        tags=["audio_session", "system"],
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        reason = str(input_data.get("reason") or "model_requested").strip() or "model_requested"
        phrase = str(input_data.get("user_close_phrase") or "").strip()
        if not _is_explicit_audio_session_close_phrase(phrase):
            return ToolResult.failed(
                ToolError(
                    "close_audio_session requires an explicit user close phrase; ordinary interruption must not close the audio session",
                    code=ErrorCode.INVALID_ARGUMENT,
                    details={"user_close_phrase": phrase or None, "reason": reason},
                )
            )
        if context.output is None or not hasattr(context.output, "close_audio_session"):
            return ToolResult.failed(ToolError("audio session close is not configured", code=ErrorCode.PROTOCOL_ERROR))
        await context.output.close_audio_session(reason=reason, close_mode="close_now")
        return ToolResult.success(
            data={"requested": True, "reason": reason},
            message="已请求关闭连续对话。",
        )


def _is_explicit_audio_session_close_phrase(phrase: str) -> bool:
    """判断模型提供的用户原话是否包含明确关闭语音会话意图。

    主要逻辑：只接受包含“对话/语音/会话/连接/聊天”等会话对象，且同时包含
    “结束/退出/关闭/停止”等关闭动作的短语；排除“算了”等普通插话。
    参数：`phrase` 为模型从用户原话中抽取的关闭短语。
    返回值：明确关闭当前连续语音会话时返回 True。
    异常情况：无。
    """

    normalized = "".join(str(phrase or "").split())
    if not normalized:
        return False
    object_tokens = ("对话", "语音", "会话", "连接", "聊天", "通话", "麦克风", "录音")
    action_tokens = ("结束", "退出", "关闭", "停止", "停掉", "断开")
    if any(action in normalized for action in action_tokens) and any(target in normalized for target in object_tokens):
        return True
    exact_phrases = {
        "不聊了",
        "先不聊了",
        "不说了",
        "先不说了",
        "到此为止",
    }
    return normalized in exact_phrases


def _coerce_tool_timeout(timeout_seconds: float | None, *, fallback: float) -> float:
    """归一 Tool 超时配置。

    主要逻辑：未声明时使用调用方给定默认值；声明值必须为正数。
    参数：`timeout_seconds` 为 Tool 显式超时，`fallback` 为默认超时。
    返回值：归一后的秒数。
    异常情况：非正数抛出 `ToolError`。
    """

    raw = fallback if timeout_seconds is None else timeout_seconds
    value = float(raw)
    if value <= 0:
        raise ToolError("tool timeout_seconds must be > 0", code=ErrorCode.INVALID_ARGUMENT)
    return value


def _effective_tool_timeout(
    spec: ToolSpec,
    *,
    default_timeout_seconds: float = TOOL_DEFAULT_TIMEOUT_SECONDS,
    max_wait_timeout_seconds: float = TOOL_MAX_WAIT_TIMEOUT_SECONDS,
) -> float:
    """返回 ToolExecutor 实际使用的超时时间。"""

    timeout_seconds = _coerce_tool_timeout(spec.timeout_seconds, fallback=default_timeout_seconds)
    max_timeout_seconds = _coerce_tool_timeout(max_wait_timeout_seconds, fallback=TOOL_MAX_WAIT_TIMEOUT_SECONDS)
    if timeout_seconds > max_timeout_seconds:
        raise ToolError(
            "tool timeout_seconds exceeds model wait timeout",
            code=ErrorCode.PROTOCOL_ERROR,
            details={"timeout_seconds": timeout_seconds, "max_wait_timeout_seconds": max_timeout_seconds},
        )
    return timeout_seconds


def _validate_tool_timeout(
    spec: ToolSpec,
    *,
    default_timeout_seconds: float = TOOL_DEFAULT_TIMEOUT_SECONDS,
    max_wait_timeout_seconds: float = TOOL_MAX_WAIT_TIMEOUT_SECONDS,
) -> None:
    """校验 Tool 注册时的短生命周期超时上限和 late result 策略。"""

    _validate_tool_late_result_policy(spec, max_wait_timeout_seconds=max_wait_timeout_seconds)
    if spec.late_result_policy == "background":
        # background 工具的最终结果由后台 runner + follow-up 路由承载，注册期不再
        # 用等待窗口约束其 timeout_seconds；其上限改由 background_timeout_seconds 表达。
        return
    _effective_tool_timeout(
        spec,
        default_timeout_seconds=default_timeout_seconds,
        max_wait_timeout_seconds=max_wait_timeout_seconds,
    )


def _validate_tool_late_result_policy(
    spec: ToolSpec,
    *,
    max_wait_timeout_seconds: float = TOOL_MAX_WAIT_TIMEOUT_SECONDS,
) -> None:
    """校验 Tool 的 late result 策略声明。

    主要逻辑：
    1. `forbidden` 工具必须保证不会超窗，等价于 `fail_fast` 的短超时约束。
    2. `background` 工具必须不在 `BACKGROUND_FORBIDDEN_TOOL_NAMES` 中，并且
       `background_timeout_seconds`（若声明）必须大于等待窗口。
    参数：`spec` 为工具规格。
    返回值：无。
    异常情况：策略非法时抛出 `ToolError`。
    """

    policy = spec.late_result_policy
    if policy not in {"background", "fail_fast", "forbidden"}:
        raise ToolError(
            f"invalid late_result_policy: {policy}",
            code=ErrorCode.INVALID_ARGUMENT,
            details={"tool": spec.name, "late_result_policy": policy},
        )
    if policy == "background":
        if spec.name in BACKGROUND_FORBIDDEN_TOOL_NAMES:
            raise ToolError(
                f"tool {spec.name} must not declare late_result_policy=background",
                code=ErrorCode.PROTOCOL_ERROR,
                details={"tool": spec.name},
            )
        if spec.background_timeout_seconds is not None:
            background_timeout = float(spec.background_timeout_seconds)
            if background_timeout <= float(max_wait_timeout_seconds):
                raise ToolError(
                    "background_timeout_seconds must exceed wait window",
                    code=ErrorCode.INVALID_ARGUMENT,
                    details={
                        "tool": spec.name,
                        "background_timeout_seconds": background_timeout,
                        "wait_window_seconds": float(max_wait_timeout_seconds),
                    },
                )


BOCHA_SEARCH_API_URL_DEFAULT = "https://api.bochaai.com/v1/web-search"
AMAP_MCP_ROUTE_TOOL = "amap.route_plan"
AMAP_MCP_GEO_TOOL = "amap.geo"


class SearchWebInput(BaseModel):
    """联网搜索 Tool 输入参数。"""

    query: str = Field(description="要搜索的问题、关键词或公开资料主题。")
    limit: int = Field(default=3, ge=1, le=10, description="最多返回的搜索结果数量。")
    freshness: str = Field(default="noLimit", description="搜索时间范围，例如 noLimit、oneDay、oneWeek、oneMonth、oneYear。")
    summary: bool = Field(default=True, description="是否请求 Bocha 返回摘要内容。")
    timeout_seconds: float = Field(default=3, gt=0, description="等待搜索结果的超时时间，单位秒。")


class SearchWebOutput(BaseModel):
    """联网搜索 Tool 输出结构。"""

    provider: str = Field(description="搜索结果来源。")
    fallback: bool = Field(description="是否进入无结果降级。")
    query: str = Field(description="实际搜索词。")
    items: list[dict] = Field(default_factory=list, description="搜索结果列表。")
    search: dict | None = Field(default=None, description="搜索返回的结构化结果。")
    error: str | None = Field(default=None, description="降级或失败原因。")


class SearchWebTool(BaseTool):
    """联网搜索 Tool。

    主要功能：通过 Bocha Web Search API 查询公开网页信息，并把结果归一成
    标题、链接、摘要、站点和发布时间，供模型组织回答。
    """

    spec = ToolSpec(
        name="search_web",
        description="当用户明确要求联网搜索、查询资料、查最新公开信息，或问题需要外部资料时调用。",
        input_model=SearchWebInput,
        output_model=SearchWebOutput,
        capability_type="tool",
        tags=["search", "web"],
        progress_message=("我查一下资料。", "稍等，我搜索一下。"),
        late_result_policy="background",
        background_timeout_seconds=30,
        follow_up_ttl_seconds=300,
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行联网搜索。

        主要逻辑：读取 `BOCHA_SEARCH_API_KEY` 和 `BOCHA_SEARCH_API_URL`，用
        Bocha Web Search API 获取结果；缺少配置或网络失败时返回明确 fallback，
        不伪装成真实搜索成功。
        参数：`context` 为 SDK 注入上下文，`input_data` 包含 query、limit、freshness。
        返回值：成功时返回归一化搜索结果。
        异常情况：参数缺失返回结构化失败；外部服务异常返回 fallback 结果。
        """

        query = str(input_data.get("query") or "").strip()
        if not query:
            return ToolResult.failed(ToolError("query is required", code=ErrorCode.INVALID_ARGUMENT))
        api_key = os.getenv("BOCHA_SEARCH_API_KEY") or os.getenv("BOCHA_API_KEY")
        if not api_key:
            return ToolResult.success(
                data={"provider": "fallback", "fallback": True, "query": query, "items": [], "error": "BOCHA_SEARCH_API_KEY is not configured"},
                message="搜索服务未配置，暂时没有搜索结果。",
            )
        try:
            search = await asyncio.to_thread(
                _call_bocha_web_search,
                api_key=api_key,
                query=query,
                count=int(input_data.get("limit") or 3),
                freshness=str(input_data.get("freshness") or "noLimit"),
                summary=bool(input_data.get("summary", True)),
                timeout_seconds=float(input_data.get("timeout_seconds") or 5),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.success(
                data={"provider": "fallback", "fallback": True, "query": query, "items": [], "error": str(exc)},
                message="搜索服务暂时不可用。",
            )
        return ToolResult.success(
            data={"provider": "bocha", "fallback": False, "query": query, "items": search["items"], "search": search},
            message="搜索完成。",
        )


def _call_bocha_web_search(
    *,
    api_key: str,
    query: str,
    count: int,
    freshness: str,
    summary: bool,
    timeout_seconds: float,
) -> dict:
    """调用 Bocha Web Search API 并归一化结果。

    主要逻辑：向 `BOCHA_SEARCH_API_URL` 指定接口发送 JSON 请求，默认使用
    Bocha 官方 Web Search 地址；响应中只保留模型回答需要的轻量字段。
    参数：`api_key/query/count/freshness/summary/timeout_seconds` 为搜索请求参数。
    返回值：包含 provider、api_url、raw 和 items 的字典。
    异常情况：HTTP、网络、JSON 或响应结构异常时抛出 RuntimeError。
    """

    api_url = os.getenv("BOCHA_SEARCH_API_URL") or BOCHA_SEARCH_API_URL_DEFAULT
    payload = {"query": query, "freshness": freshness, "summary": summary, "count": max(1, min(10, int(count)))}
    request = urllib_request.Request(
        api_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Bocha 搜索 HTTP {exc.code}: {detail}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"Bocha 搜索网络失败：{exc.reason}") from exc
    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Bocha 搜索返回非 JSON：{exc}") from exc
    pages = (((raw.get("data") or {}).get("webPages") or {}).get("value") or [])
    if not isinstance(pages, list):
        raise RuntimeError("Bocha 搜索返回结构缺少 data.webPages.value[]")
    items = [_normalize_bocha_page(page) for page in pages[: payload["count"]] if isinstance(page, dict)]
    return {"provider": "bocha", "api_url": api_url, "raw": raw, "items": items}


def _normalize_bocha_page(page: dict) -> dict:
    """把 Bocha 单条网页结果归一成模型更容易消费的结构。"""

    return {
        "title": str(page.get("name") or page.get("title") or "").strip(),
        "url": str(page.get("url") or "").strip(),
        "snippet": str(page.get("snippet") or "").strip(),
        "summary": str(page.get("summary") or page.get("content") or "").strip(),
        "site_name": str(page.get("siteName") or "").strip(),
        "date_published": str(page.get("datePublished") or "").strip(),
    }


class QuerySystemTimeInput(BaseModel):
    """系统时间查询 Tool 输入参数。"""

    timezone: str = Field(
        default="Asia/Shanghai",
        description="要查询的时区，默认 Asia/Shanghai；也可传 UTC+8、+08:00、America/New_York 等。",
    )


class QuerySystemTimeOutput(BaseModel):
    """系统时间查询 Tool 输出结构。"""

    timezone: str = Field(description="实际使用的时区名称或固定偏移。")
    iso_datetime: str = Field(description="带时区偏移的 ISO 8601 时间。")
    date: str = Field(description="本地日期，YYYY-MM-DD。")
    time: str = Field(description="本地时间，HH:MM:SS。")
    weekday: str = Field(description="英文星期名称。")
    utc_offset: str = Field(description="UTC 偏移，例如 +08:00。")
    unix_timestamp: int = Field(description="Unix 秒级时间戳。")


class QuerySystemTimeTool(BaseTool):
    """查询当前系统时间的前台 Tool。

    主要功能：读取服务端当前系统时间，并按用户指定时区返回结构化时间。
    主要方法：`run()` 负责解析时区、格式化当前时间并返回 ToolResult。
    主要属性：`spec` 定义模型可见的工具名、描述和输入输出结构。
    """

    spec = ToolSpec(
        name="query_system_time",
        description="当用户询问当前时间、日期、星期、某个时区现在几点，或需要时间上下文时调用。默认返回北京时间。",
        input_model=QuerySystemTimeInput,
        output_model=QuerySystemTimeOutput,
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """查询指定时区的当前系统时间。

        主要逻辑：解析 `timezone` 参数；支持 IANA 时区名和 UTC 固定偏移；使用
        `datetime.now()` 读取服务端当前时间并转换到目标时区。
        参数：`context` 是 SDK 注入上下文；`input_data` 包含可选 `timezone`。
        返回值：成功时返回日期、时间、ISO 时间、UTC 偏移和时间戳。
        异常情况：时区无法识别时返回结构化参数错误，不向上抛出异常。
        """

        del context
        timezone_text = str(input_data.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai"
        try:
            tzinfo, timezone_name = _parse_timezone(timezone_text)
        except ValueError as exc:
            return ToolResult.failed(ToolError(str(exc), code=ErrorCode.INVALID_ARGUMENT))
        now = datetime.now(tzinfo)
        offset = now.utcoffset() or timedelta(0)
        data = {
            "timezone": timezone_name,
            "iso_datetime": now.isoformat(timespec="seconds"),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "weekday": now.strftime("%A"),
            "utc_offset": _format_utc_offset(offset),
            "unix_timestamp": int(now.timestamp()),
        }
        return ToolResult.success(data=data, message=f"{timezone_name} 当前时间 {data['iso_datetime']}")


class QueryCurrentLocationInput(BaseModel):
    """当前位置查询 Tool 输入参数。"""

    timeout_seconds: float = Field(default=3, gt=0, description="等待端侧 GPS/定位回报的最长时间，单位秒。")
    high_accuracy: bool = Field(default=True, description="是否请求端侧尽量使用高精度定位。")


class QueryCurrentLocationOutput(BaseModel):
    """当前位置查询 Tool 输出结构。"""

    location_ready: bool = Field(description="是否拿到可用于导航的当前位置。")
    provider: str = Field(description="定位来源，例如 device_gps、no_capable_device、timeout。")
    latitude: float | None = Field(default=None, description="纬度。")
    longitude: float | None = Field(default=None, description="经度。")
    accuracy_meters: float | None = Field(default=None, description="定位精度，单位米。")
    error: str | None = Field(default=None, description="失败或不可用原因。")


class QueryCurrentLocationTool(BaseTool):
    """请求端侧当前位置的前台 Tool。

    主要功能：向已声明定位能力的端侧发送一次定位命令，并等待 GPS/浏览器定位回报。
    主要方法：`run()` 先检查是否有端侧可消费定位命令，再调用 `commands.call()`。
    主要属性：`spec` 定义模型可见工具名、说明和输入输出结构。
    """

    spec = ToolSpec(
        name="query_current_location",
        description="仅当用户主动询问“我在哪里”或当前位置时调用。路线规划从当前位置出发时，优先直接调用 query_route_plan 并把 origin 设为“当前位置”。",
        input_model=QueryCurrentLocationInput,
        output_model=QueryCurrentLocationOutput,
        capability_type="tool",
        tags=["location", "gps", "navigation"],
        progress_message=("我先获取一下当前位置。", "稍等，我请求一下设备定位。"),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """请求端侧当前位置。

        主要逻辑：先根据设备注册 properties 判断是否有端侧声明可消费定位命令；
        没有则立即返回提醒；有则发送 `device.location.get_current` 标准命令并等待终态。
        参数：`context` 为 SDK 注入上下文；`input_data` 包含超时和高精度偏好。
        返回值：成功时返回经纬度和精度；失败、未授权、无人消费或超时时返回结构化原因。
        异常情况：设备选择或命令等待异常会转成 ToolResult 成功态下的不可用数据，
        便于模型继续追问用户。
        """

        timeout_seconds = float(input_data.get("timeout_seconds") or 6)
        high_accuracy = bool(input_data.get("high_accuracy", True))
        return await _request_device_location(context, timeout_seconds=timeout_seconds, high_accuracy=high_accuracy)


async def _request_device_location(context: ToolContext, *, timeout_seconds: float, high_accuracy: bool) -> ToolResult:
    """请求端侧返回一次当前位置。

    主要逻辑：先检查当前用户是否有在线设备以及设备是否声明定位能力；没有可消费端
    侧时立即返回，不等待命令超时。有可消费端侧时发送标准定位命令并解析经纬度。
    参数：`context` 是 Tool 上下文；`timeout_seconds` 是等待端侧回报的最长时间；
    `high_accuracy` 表示是否请求端侧尽量使用高精度定位。
    返回值：ToolResult 成功态，data.location_ready 表示定位是否可用。
    异常情况：命令派发、超时、授权失败和结果格式异常都会转成可读 message。
    """

    devices = list(context.devices._get_devices())
    capable_devices = [device for device in devices if _device_supports_location(device)]
    if not devices:
        return ToolResult.success(
            data={
                "location_ready": False,
                "provider": "no_active_device",
                "latitude": None,
                "longitude": None,
                "accuracy_meters": None,
                "error": "当前用户没有在线设备，无法请求 GPS 定位。",
            },
            message="当前没有在线设备，无法获取当前位置。请告诉我出发地点。",
        )
    if not capable_devices:
        return ToolResult.success(
            data={
                "location_ready": False,
                "provider": "no_capable_device",
                "latitude": None,
                "longitude": None,
                "accuracy_meters": None,
                "error": "在线设备没有声明可消费定位请求。",
            },
            message="当前设备没有声明定位能力，请在端侧开启定位能力，或直接告诉我出发地点。",
        )
    selector = {"realtime_agent.location": True}
    try:
        result = await context.devices.commands.call(
            name="device.location.get_current",
            selector=selector,
            params={"high_accuracy": high_accuracy},
            timeout_seconds=timeout_seconds,
            require_single=True,
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.success(
            data={
                "location_ready": False,
                "provider": "command_error",
                "latitude": None,
                "longitude": None,
                "accuracy_meters": None,
                "error": str(exc),
            },
            message="请求当前位置失败，请告诉我明确的出发地点。",
        )
    if not result.ok:
        reason = _location_command_error_message(result) or "定位请求未完成。"
        return ToolResult.success(
            data={
                "location_ready": False,
                "provider": "device_location_failed",
                "latitude": None,
                "longitude": None,
                "accuracy_meters": None,
                "command_id": result.command_id,
                "errors": result.errors,
                "error": reason,
            },
            message=f"没有获取到当前位置：{reason} 请告诉我明确的出发地点。",
        )
    location = _extract_location_from_command_result(result)
    if location is None:
        return ToolResult.success(
            data={
                "location_ready": False,
                "provider": "invalid_device_location",
                "latitude": None,
                "longitude": None,
                "accuracy_meters": None,
                "command_id": result.command_id,
                "error": "端侧定位结果缺少可用经纬度。",
            },
            message="端侧返回的定位结果不完整，请告诉我明确的出发地点。",
        )
    return ToolResult.success(
        data={
            "location_ready": True,
            "provider": "device_gps",
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "accuracy_meters": location.get("accuracy_meters"),
            "timestamp_ms": location.get("timestamp_ms"),
            "command_id": result.command_id,
            "raw": location.get("raw") or {},
            "error": None,
        },
        message=f"已获取当前位置：{location['longitude']},{location['latitude']}",
    )


def _device_supports_location(device: DeviceSnapshot) -> bool:
    """判断设备是否声明可消费当前位置请求。"""

    props = dict(device.properties or {})
    commands = props.get("realtime_agent.location_commands") or []
    if isinstance(commands, str):
        commands = [commands]
    return bool(props.get("realtime_agent.location")) or "device.location.get_current" in {str(item) for item in commands}


def _location_command_error_message(result: CommandResult) -> str:
    """从定位命令失败结果中提取用户可理解的原因。"""

    messages: list[str] = []
    for error in result.errors:
        data = dict(error.get("data") or {})
        nested_error = data.get("error") if isinstance(data.get("error"), dict) else {}
        message = (
            str(nested_error.get("message") or "")
            or str(data.get("message") or "")
            or str(error.get("message") or "")
        ).strip()
        if message:
            messages.append(message)
    return "；".join(messages)


def _extract_location_from_command_result(result: CommandResult) -> dict | None:
    """从 `CommandResult` 中提取端侧定位数据。

    主要逻辑：读取最后一个 completed 事件，兼容端侧把经纬度放在顶层、`location`
    或 `coords` 字段中的写法。
    参数：`result` 是 `commands.call()` 返回的聚合结果。
    返回值：包含 latitude、longitude、accuracy_meters 的字典；无法解析时返回 None。
    异常情况：无。
    """

    completed = [event for event in result.data.get("events") or [] if event.get("state") == "completed"]
    if not completed:
        return None
    data = dict(completed[-1].get("data") or {})
    payload = data.get("location") if isinstance(data.get("location"), dict) else data
    coords = payload.get("coords") if isinstance(payload.get("coords"), dict) else payload
    latitude = coords.get("latitude") or coords.get("lat")
    longitude = coords.get("longitude") or coords.get("lng") or coords.get("lon")
    try:
        lat = float(latitude)
        lng = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    accuracy = coords.get("accuracy") or coords.get("accuracy_meters") or coords.get("accuracyMeters")
    try:
        accuracy_meters = float(accuracy) if accuracy is not None else None
    except (TypeError, ValueError):
        accuracy_meters = None
    return {
        "latitude": lat,
        "longitude": lng,
        "accuracy_meters": accuracy_meters,
        "timestamp_ms": data.get("timestamp_ms") or payload.get("timestamp_ms") or payload.get("timestamp"),
        "raw": data,
    }


def _parse_timezone(value: str) -> tuple[timezone | ZoneInfo, str]:
    """解析时区字符串。

    主要逻辑：先处理北京时间、东八区、UTC/GMT 和固定偏移，再按 IANA 时区名加载。
    参数：`value` 是模型传入的时区文本。
    返回值：Python `tzinfo` 对象和规范化后的时区名。
    异常情况：无法识别时抛出 `ValueError`。
    """

    normalized = value.strip() or "Asia/Shanghai"
    alias_map = {
        "北京时间": "Asia/Shanghai",
        "北京": "Asia/Shanghai",
        "东八区": "UTC+08:00",
        "utc": "UTC",
        "z": "UTC",
    }
    normalized = alias_map.get(normalized.lower()) or alias_map.get(normalized) or normalized
    upper = normalized.upper()
    if upper in {"UTC", "GMT"}:
        return timezone.utc, "UTC"
    if upper.startswith("UTC") or upper.startswith("GMT") or normalized.startswith(("+", "-")):
        offset_text = normalized[3:] if upper.startswith(("UTC", "GMT")) else normalized
        delta = _parse_utc_offset(offset_text)
        return timezone(delta), f"UTC{_format_utc_offset(delta)}"
    try:
        return ZoneInfo(normalized), normalized
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"无法识别时区：{value}。请使用 Asia/Shanghai、UTC+8 或 +08:00 这类格式。") from exc


def _parse_utc_offset(value: str) -> timedelta:
    """解析 UTC 偏移字符串。

    主要逻辑：支持 +8、+08、+08:00、-05:30 等常见写法，并限制在合法 UTC
    偏移范围内。
    参数：`value` 是去掉 UTC/GMT 前缀后的偏移文本。
    返回值：对应的时间偏移。
    异常情况：格式非法或超出范围时抛出 `ValueError`。
    """

    text = value.strip()
    if not text:
        return timedelta(0)
    sign_text = text[0]
    if sign_text not in {"+", "-"}:
        raise ValueError(f"无法识别 UTC 偏移：{value}")
    body = text[1:]
    if ":" in body:
        hour_text, minute_text = body.split(":", 1)
    else:
        hour_text, minute_text = body, "0"
    if not hour_text.isdigit() or not minute_text.isdigit():
        raise ValueError(f"无法识别 UTC 偏移：{value}")
    hours = int(hour_text)
    minutes = int(minute_text)
    if hours > 14 or minutes >= 60 or (hours == 14 and minutes != 0):
        raise ValueError(f"UTC 偏移超出合法范围：{value}")
    delta = timedelta(hours=hours, minutes=minutes)
    return delta if sign_text == "+" else -delta


def _format_utc_offset(offset: timedelta) -> str:
    """格式化 UTC 偏移。

    主要逻辑：把 `timedelta` 转成 `+HH:MM` 或 `-HH:MM`。
    参数：`offset` 是时区偏移。
    返回值：固定宽度偏移字符串。
    异常情况：无。
    """

    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"{sign}{hours:02d}:{minutes:02d}"


class QueryRoutePlanInput(BaseModel):
    """路线规划 Tool 输入参数。"""

    destination: str = Field(description="用户想去的目的地名称、地址或经纬度。")
    origin: str = Field(default="当前位置", description="导航起点；可填地址、地点名、经纬度或“当前位置”。传入“当前位置”时会先请求端侧定位。")
    timeout_seconds: float = Field(default=45, gt=0, description="路线规划在后台的整体最大耗时预算，单位秒。该工具是后台能力：超过 3 秒未完成会先返回“正在规划”，结果稍后送达。")


class QueryRoutePlanOutput(BaseModel):
    """路线规划 Tool 输出结构。"""

    route_ready: bool = Field(description="是否准备好可用路线。")
    provider: str = Field(description="路线来源。")
    destination: str = Field(description="导航目的地。")
    origin: str = Field(description="导航起点。")
    route: dict | None = Field(default=None, description="MCP 返回的路线结构化结果。")
    error: str | None = Field(default=None, description="失败或 fallback 原因。")


class QueryRoutePlanTool(BaseTool):
    """AMap MCP 路线规划 Tool。

    主要功能：通过 `context.mcp` 或 `AMAP_MCP_*` 环境变量构造的 MCP Gateway
    调用 AMap 路线规划能力。地址会先尝试用 AMap geo 工具转成经纬度。
    """

    spec = ToolSpec(
        name="query_route_plan",
        description="当用户想去某个地点、询问怎么走或需要路线规划时调用。可以把 origin 设为“当前位置”，工具会先请求端侧定位；定位失败时会返回原因并提示模型向用户确认起点。",
        input_model=QueryRoutePlanInput,
        output_model=QueryRoutePlanOutput,
        capability_type="mcp",
        tags=["navigation", "amap", "mcp"],
        progress_message=("我先规划一下路线。", "稍等，我查一下怎么走。"),
        late_result_policy="background",
        background_timeout_seconds=60,
        follow_up_ttl_seconds=300,
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行路线规划。

        主要逻辑：优先复用应用配置的 MCP Gateway；如果未启用或未配置 AMap，
        则从 `AMAP_MCP_URL`、`AMAP_MCP_BEARER_TOKEN`、`AMAP_MCP_API_KEY`
        临时构造 Gateway 调用 `maps_geo` 和 `maps_direction_walking`。
        参数：`context` 为 SDK 注入上下文，`input_data` 包含 origin/destination。
        返回值：成功时返回路线和地理编码信息；失败时返回 fallback 结构。
        异常情况：目的地缺失返回结构化失败；外部 MCP 异常返回 fallback。
        """

        destination = str(input_data.get("destination") or "").strip()
        origin = str(input_data.get("origin") or "当前位置").strip() or "当前位置"
        # 后台能力：整体耗时预算放宽到后台总超时量级，前台 3 秒窗口由 ToolExecutor 负责。
        timeout_seconds = float(input_data.get("timeout_seconds") or 45)
        deadline = time.monotonic() + timeout_seconds
        if not destination:
            return ToolResult.failed(ToolError("destination is required", code=ErrorCode.INVALID_ARGUMENT))
        origin_location_source: str | None = None
        origin_location_accuracy: float | None = None
        if _is_ambiguous_current_location(origin):
            location_timeout = min(1.0, _remaining_seconds(deadline))
            location_result = await _request_device_location(
                context,
                timeout_seconds=location_timeout,
                high_accuracy=True,
            )
            if not location_result.data.get("location_ready"):
                return ToolResult.success(
                    data={
                        "route_ready": False,
                        "provider": "needs_origin",
                        "origin": origin,
                        "destination": destination,
                        "route": None,
                        "location": location_result.data,
                        "error": location_result.data.get("error") or location_result.message,
                        "needs_origin": True,
                    },
                    message=f"{location_result.message} 你可以询问用户明确的出发地点后再规划路线。",
                )
            origin = f"{location_result.data['longitude']},{location_result.data['latitude']}"
            origin_location_source = str(location_result.data.get("provider") or "device_gps")
            origin_location_accuracy = location_result.data.get("accuracy_meters")
        try:
            mcp = _resolve_amap_mcp_gateway(getattr(context, "mcp", None))
            origin_location, origin_geo = await _route_point_for_mcp(
                mcp,
                value=origin,
                label="起点",
                deadline=deadline,
            )
            destination_location, destination_geo = await _route_point_for_mcp(
                mcp,
                value=destination,
                label="终点",
                deadline=deadline,
            )
            route = await _call_mcp_with_deadline(
                mcp,
                tool_name=AMAP_MCP_ROUTE_TOOL,
                arguments={"origin": origin_location, "destination": destination_location},
                deadline=deadline,
            )
            route_error = _mcp_result_error(route)
            if route_error:
                return ToolResult.success(
                    data={
                        "route_ready": False,
                        "provider": "amap_mcp",
                        "origin": origin,
                        "destination": destination,
                        "origin_location": origin_location,
                        "destination_location": destination_location,
                        "origin_location_source": origin_location_source,
                        "origin_location_accuracy_meters": origin_location_accuracy,
                        "origin_geo": origin_geo,
                        "destination_geo": destination_geo,
                        "route": route,
                        "error": route_error,
                    },
                    message="路线规划失败，请补充更明确的起点或换一种出行方式。",
                )
        except (RoutePlanTimeoutError, TimeoutError, asyncio.TimeoutError) as exc:
            return ToolResult.success(
                data={
                    "route_ready": False,
                    "provider": "timeout",
                    "origin": origin,
                    "destination": destination,
                    "route": None,
                    "error": str(exc) or "路线规划超过等待时间。",
                },
                message="路线规划超时，请稍后重试，或告诉我更精确的起点和目的地。",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.success(
                data={
                    "route_ready": False,
                    "provider": "fallback",
                    "origin": origin,
                    "destination": destination,
                    "route": None,
                    "error": str(exc),
                },
                message="路线规划服务暂时不可用。",
            )
        return ToolResult.success(
            data={
                "route_ready": True,
                "provider": "amap_mcp",
                "origin": origin,
                "destination": destination,
                "origin_location": origin_location,
                "destination_location": destination_location,
                "origin_location_source": origin_location_source,
                "origin_location_accuracy_meters": origin_location_accuracy,
                "origin_geo": origin_geo,
                "destination_geo": destination_geo,
                "route": route,
            },
            message="路线已准备。",
        )


def _resolve_amap_mcp_gateway(configured_gateway: Any) -> Any:
    """返回可调用 AMap 的 MCP Gateway。

    主要逻辑：如果应用已配置 MCP，优先使用；否则根据 `AMAP_MCP_*` 环境变量
    构造一个只包含 AMap geo 和步行路线规划的临时 Gateway。
    参数：`configured_gateway` 为 ToolContext 注入的 MCP Gateway。
    返回值：可调用 `amap.geo` 和 `amap.route_plan` 的对象。
    异常情况：环境变量不足时后续 MCP 调用会返回明确错误。
    """

    if configured_gateway is not None:
        try:
            tool_names = {tool.name for tool in configured_gateway.list_tools()}
            if AMAP_MCP_ROUTE_TOOL in tool_names:
                return configured_gateway
        except Exception:
            pass
    headers = {}
    bearer_token = os.getenv("AMAP_MCP_BEARER_TOKEN") or ""
    api_key = os.getenv("AMAP_MCP_API_KEY") or ""
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key:
        headers["x-api-key"] = api_key
    gateway = McpGateway(enabled=True)
    gateway.register_server(
        McpServerSpec(
            name="amap",
            transport="streamable_http",
            url=os.getenv("AMAP_MCP_URL") or "",
            headers=headers,
        )
    )
    gateway.register_tool(McpToolSpec(name=AMAP_MCP_GEO_TOOL, server="amap", target_name="maps_geo"))
    gateway.register_tool(McpToolSpec(name=AMAP_MCP_ROUTE_TOOL, server="amap", target_name="maps_direction_walking"))
    return gateway


def _looks_like_lnglat(value: str) -> bool:
    """判断字符串是否已经是 `经度,纬度` 坐标。"""

    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        return False
    try:
        longitude = float(parts[0])
        latitude = float(parts[1])
    except ValueError:
        return False
    return -180 <= longitude <= 180 and -90 <= latitude <= 90


def _is_ambiguous_current_location(value: str) -> bool:
    """判断起点是否只是无法解析的当前位置占位。

    主要逻辑：当前 SDK 内置路线工具没有接入设备定位，因此“当前位置”等占位词
    不能被当作真实地址送给 AMap geocode，否则容易被错误解析成同名地点。
    参数：`value` 为模型传入的起点。
    返回值：属于当前位置占位时返回 True。
    异常情况：无。
    """

    normalized = value.strip().lower()
    return normalized in {"", "当前位置", "当前地点", "我的位置", "我现在的位置", "current location", "here"}


def _remaining_seconds(deadline: float) -> float:
    """读取距离整体超时截止点还剩多少秒。"""

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RoutePlanTimeoutError("路线规划超过整体等待时间。")
    return remaining


class RoutePlanTimeoutError(Exception):
    """路线规划整体或外部服务超时。"""


def _invoke_mcp_call(mcp: Any, *, tool_name: str, arguments: dict, timeout_seconds: float) -> dict:
    """在线程中调用 MCP，并把底层 TimeoutError 转成路线规划专用异常。"""

    try:
        return mcp.call(tool_name=tool_name, arguments=arguments, timeout_seconds=timeout_seconds)
    except TimeoutError as exc:
        raise RoutePlanTimeoutError(str(exc)) from exc


async def _call_mcp_with_deadline(mcp: Any, *, tool_name: str, arguments: dict, deadline: float) -> dict:
    """按整体截止时间调用一次 MCP tool。

    主要逻辑：MCP Streamable HTTP 调用内部包含 initialize、initialized 和
    tools/call 多次 HTTP 请求。这里把每次 HTTP 请求的 timeout 压低，并在外层
    使用整体 deadline 限制前台 Tool 的等待时间。
    参数：`mcp` 为 MCP Gateway；`tool_name/arguments` 为工具调用；`deadline`
    为路线规划整体截止时间。
    返回值：MCP 调用结果。
    异常情况：剩余时间不足或外层等待超时时抛出 TimeoutError。
    """

    remaining = _remaining_seconds(deadline)
    per_http_timeout = max(0.2, remaining)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _invoke_mcp_call,
                mcp,
                tool_name=tool_name,
                arguments=arguments,
                timeout_seconds=per_http_timeout,
            ),
            timeout=remaining,
        )
    except asyncio.TimeoutError as exc:
        raise RoutePlanTimeoutError("路线规划超过整体等待时间。") from exc


async def _route_point_for_mcp(mcp: Any, *, value: str, label: str, deadline: float) -> tuple[str, dict | None]:
    """把地址、地点名或坐标转换成 AMap 路线规划可用坐标。"""

    if _looks_like_lnglat(value):
        return value, None
    geo_result = await _call_mcp_with_deadline(
        mcp,
        tool_name=AMAP_MCP_GEO_TOOL,
        arguments={"address": value},
        deadline=deadline,
    )
    return _extract_geo_location(geo_result, label)


def _extract_geo_location(call_result: dict, label: str) -> tuple[str, dict]:
    """从 AMap geo MCP 结果中提取第一个可用坐标。"""

    parsed = _mcp_text_json(call_result)
    candidates = parsed.get("return") or parsed.get("geocodes") or []
    if isinstance(candidates, dict):
        candidates = [candidates]
    if not isinstance(candidates, list):
        raise ValueError(f"{label}地理编码结果格式不正确")
    for item in candidates:
        if not isinstance(item, dict):
            continue
        location = str(item.get("location") or item.get("lnglat") or "").strip()
        if _looks_like_lnglat(location):
            return location, item
    raise ValueError(f"{label}未找到可用坐标")


def _mcp_result_error(call_result: dict) -> str:
    """提取 MCP 调用结果中的错误文本。

    主要逻辑：MCP tool 可能把业务错误放在 `result.isError=true` 和 content 文本中，
    这种结果不能当成可用路线。
    参数：`call_result` 是 `McpGateway.call()` 返回值。
    返回值：有错误时返回文本；没有错误时返回空字符串。
    异常情况：无。
    """

    result = dict(call_result.get("result") or call_result)
    if not result.get("isError"):
        return ""
    return _mcp_first_text(result) or "mcp tool returned error"


def _mcp_text_json(call_result: dict) -> dict:
    """从 MCP `content[text]` 或直接 JSON 响应中解析对象。"""

    result = dict(call_result.get("result") or call_result)
    if result.get("isError"):
        raise ValueError(_mcp_first_text(result) or "mcp tool returned error")
    for item in result.get("content") or []:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    if isinstance(result, dict):
        return result
    raise ValueError("mcp response has no json object")


def _mcp_first_text(result: dict) -> str:
    """读取 MCP result 中第一段文本，主要用于错误说明。"""

    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            return str(item.get("text") or "").strip()
    return ""


class SearchConversationHistoryInput(BaseModel):
    """历史对话检索 Tool 输入参数。"""

    query: str = Field(default="", description="要检索的关键词；留空时返回最近历史消息。")
    session_id: str = Field(default="", description="可选会话编号；留空时检索当前用户所有会话。")
    limit: int = Field(default=5, ge=1, le=20, description="最多返回多少条历史片段。")


class SearchConversationHistoryTool(BaseTool):
    """历史对话检索 Tool。

    主要功能：只读扫描 runs 目录中的 `messages.jsonl`，按用户、会话和关键词
    返回轻量历史片段，供模型回答“我刚才说过什么”等问题。
    """

    spec = ToolSpec(
        name="search_conversation_history",
        description="当用户询问历史对话、刚才说过什么、之前提到的信息或需要从 runs 产物检索历史消息时调用。",
        input_model=SearchConversationHistoryInput,
        output_model=dict,
        capability_type="tool",
        tags=["history", "runs"],
        progress_message=("我查一下历史对话。", "稍等，我看一下之前的记录。"),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """检索历史对话。

        主要逻辑：从上下文 metadata 读取 runs_root，只读当前 user_id 目录下的
        `messages.jsonl`；关键词匹配采用轻量包含匹配，不解析或修改运行产物。
        参数：`context` 为 SDK 注入上下文，`input_data` 包含 query、session_id、limit。
        返回值：匹配消息片段和来源路径。
        异常情况：runs_root 不存在时返回空结果。
        """

        runs_root = Path(str((context.metadata or {}).get("runs_root") or "runs/default-app")).expanduser()
        query = str(input_data.get("query") or "").strip()
        session_id = str(input_data.get("session_id") or "").strip()
        limit = max(1, min(20, int(input_data.get("limit") or 5)))
        matches = await asyncio.to_thread(
            _search_conversation_messages,
            runs_root=runs_root,
            user_id=context.user_id,
            session_id=session_id,
            query=query,
            limit=limit,
        )
        message = "已找到历史对话。" if matches else "没有找到匹配的历史对话。"
        return ToolResult.success(
            data={"query": query, "session_id": session_id or None, "matches": matches, "count": len(matches)},
            message=message,
        )


def _search_conversation_messages(
    *,
    runs_root: Path,
    user_id: str,
    session_id: str,
    query: str,
    limit: int,
) -> list[dict]:
    """扫描 runs 中的 messages.jsonl 并返回匹配片段。"""

    user_root = runs_root / user_id
    if not user_root.is_dir():
        return []
    files = [user_root / session_id / "messages.jsonl"] if session_id else list(user_root.glob("*/messages.jsonl"))
    existing = [path for path in files if path.is_file()]
    existing.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    rows: list[dict] = []
    query_lower = query.lower()
    for path in existing:
        for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = _message_record_text(record)
            if query_lower and query_lower not in text.lower():
                continue
            rows.append(
                {
                    "session_id": path.parent.name,
                    "line": line_number,
                    "role": str(record.get("role") or record.get("speaker") or ""),
                    "text": text[:1000],
                    "source": str(path),
                }
            )
            if len(rows) >= limit:
                return rows
    return rows


def _message_record_text(record: Any) -> str:
    """从不同 messages.jsonl 记录形态中提取可检索文本。"""

    if isinstance(record, str):
        return record
    if not isinstance(record, dict):
        return ""
    candidates = [record.get("text"), record.get("transcript"), record.get("content"), record.get("message")]
    parts: list[str] = []
    for value in candidates:
        parts.extend(_text_fragments(value))
    return "\n".join(part for part in parts if part).strip()


def _text_fragments(value: Any) -> list[str]:
    """递归提取消息 content 中的文本片段。"""

    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        parts = []
        for key in ("text", "content", "transcript", "message"):
            parts.extend(_text_fragments(value.get(key)))
        return parts
    if isinstance(value, list):
        parts = []
        for item in value:
            parts.extend(_text_fragments(item))
        return parts
    return []


class MemorySearchTool(BaseTool):
    """按主题读取长期记忆详情的内置 Tool。"""

    class Input(BaseModel):
        topic: str = Field(
            default="",
            description="要读取详情的单个记忆主题；优先使用系统提示中列出的记忆主题，或用户明确提到的记忆主题。",
        )
        topics: list[str] = Field(
            default_factory=list,
            description="要一次读取详情的多个记忆主题；只填写与当前回答直接相关的主题。",
        )

    spec = ToolSpec(
        name="memory_search",
        description=(
            "当回答用户问题需要读取已保存的长期记忆详情时调用。"
            "本工具只查询记忆，不用于新增、更新或删除记忆；维护记忆请使用 manage_memory。"
        ),
        input_model=Input,
        capability_type="tool",
        tags=["memory"],
        progress_message=(
            "我先查一下记忆。",
            "我看看之前记了什么。",
            "稍等，我翻一下记忆。",
        ),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.memory is None:
            return ToolResult.failed(ToolError("memory service is not configured", code=ErrorCode.PROTOCOL_ERROR))
        topics = list(input_data.get("topics") or [])
        topic = str(input_data.get("topic") or "").strip()
        if topic:
            topics.insert(0, topic)
        records = context.memory.search_by_topics(user_id=context.user_id, topics=topics)
        memories = [memory_record_to_public_dict(record) for record in records]
        feedback = "已读取记忆详情" if memories else "没有找到匹配的记忆"
        return ToolResult.success(
            data={"memories": memories, "feedback": feedback},
            message=feedback,
        )


class ManageMemoryTool(BaseTool):
    """管理长期记忆的模型可见入口。

    主要功能：把主 Agent 提取出的 `memory_context` 交给系统级 `MemoryService`。
    记忆判断和落盘不在 Tool 内完成，而由 MemoryService 调用内部记忆管理子 Agent
    生成动作计划并执行，确保记忆能力不绑定某个业务模型或单个 Tool 实现。
    """

    class Input(BaseModel):
        memory_context: str = Field(
            description=(
                "请填写本轮对话中与长期记忆维护有关的信息，可以是用户原话，或者抽取出来需要记住或更新的事实、"
                "用户要求忘记或删除的内容，以及必要的上下文。"
            ),
        )

    spec = ToolSpec(
        name="manage_memory",
        description=(
            "当用户要求记住、更新、忘记或删除信息，或自然提供了姓名、偏好、习惯等值得长期保存的信息时调用。"
            "本工具只用于维护记忆，不用于查询记忆；查询已有记忆请使用 memory_search。"
        ),
        input_model=Input,
        capability_type="tool",
        tags=["memory"],
        progress_message=(
            "嗯，我先记一下。",
            "好，我把这个记下来。",
            "明白，我先帮你记录一下。",
        ),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行长期记忆维护。

        主要逻辑：Tool 只做模型入参归一和错误返回；真正的记忆管理由
        `context.memory.manage()` 负责。`context.memory` 是应用级系统服务，
        内部会读取当前 user_id 的已有记忆、调用 MemoryManagementAgent，再执行
        add/update/delete 动作。
        """

        if context.memory is None:
            return ToolResult.failed(ToolError("memory service is not configured", code=ErrorCode.PROTOCOL_ERROR))
        memory_context = str(
            input_data.get("memory_context")
            or input_data.get("content")
            or input_data.get("query")
            or ""
        ).strip()
        if not memory_context:
            return ToolResult.failed(ToolError("memory_context is required", code=ErrorCode.INVALID_ARGUMENT))
        result = context.memory.manage(
            user_id=context.user_id,
            memory_context=memory_context,
            metadata={"session_id": context.session_id},
        )
        feedback = str(result.get("feedback") or "记忆已处理")
        return ToolResult.success(data=result, message=feedback)


class ReadSkillTool(BaseTool):
    """读取受控 Skill 的内置 Tool。"""

    class Input(BaseModel):
        name: str = Field(description="要读取的 Skill 名称。")

    spec = ToolSpec(
        name="read_skill",
        description="当当前任务需要了解某个 Skill 的能力边界、调用步骤或可用工具时调用。",
        input_model=Input,
        capability_type="tool",
        tags=["skill"],
        progress_message=(
            "我先看一下这个技能说明。",
            "稍等，我读取一下技能文档。",
        ),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.skills is None:
            return ToolResult.failed(ToolError("skill service is not configured", code=ErrorCode.PROTOCOL_ERROR))
        document = context.skills.read_skill(str(input_data.get("name") or ""))
        return ToolResult.success(data=asdict(document), message=document.description)


class McpCallTool(BaseTool):
    """通过 MCP Gateway 调用 MCP tool 的内置 Tool。"""

    class Input(BaseModel):
        tool_name: str = Field(description="外部工具名称，例如 web.search 或 amap.route_plan。")
        arguments: dict = Field(default_factory=dict, description="传给外部工具的结构化参数。")
        timeout_seconds: float | None = Field(default=None, gt=0, description="调用超时时间，单位秒。")

    spec = ToolSpec(
        name="mcp_call",
        description="调用已配置的外部工具。优先使用更具体的业务工具；只有没有专用工具时才调用本工具。",
        input_model=Input,
        capability_type="mcp",
        tags=["mcp"],
        late_result_policy="background",
        background_timeout_seconds=30,
        follow_up_ttl_seconds=300,
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.mcp is None:
            return ToolResult.failed(ToolError("mcp gateway is not configured", code=ErrorCode.PROTOCOL_ERROR))
        result = await asyncio.to_thread(
            context.mcp.call,
            tool_name=str(input_data.get("tool_name") or ""),
            arguments=dict(input_data.get("arguments") or {}),
            timeout_seconds=input_data.get("timeout_seconds"),
        )
        return ToolResult.success(data=result, message="mcp tool called")


BUILTIN_TOOLS = (
    QueryDeviceStateTool,
    ToolRunManagerTool,
    TimerTool,
    CloseAudioSessionTool,
    SearchWebTool,
    QuerySystemTimeTool,
    QueryCurrentLocationTool,
    QueryRoutePlanTool,
    MemorySearchTool,
    ManageMemoryTool,
    SearchConversationHistoryTool,
)

EXTENSION_BUILTIN_TOOLS = (
    ReadSkillTool,
    McpCallTool,
)

SYSTEM_CONTEXT_TOOL_NAMES = {
    ToolRunManagerTool.spec.name,
    MemorySearchTool.spec.name,
    ManageMemoryTool.spec.name,
    SearchConversationHistoryTool.spec.name,
    ReadSkillTool.spec.name,
    McpCallTool.spec.name,
}
