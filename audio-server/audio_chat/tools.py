from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, Field, ValidationError

from audio_chat.asset import ArtifactRef, AssetRef
from audio_chat.control import PublishResult
from audio_chat.errors import AudioChatError, ErrorCode
from audio_chat.memory import memory_record_to_public_dict
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, EventName, StreamChunk, StreamFormat, StreamType, new_id


class DeviceNotFoundError(AudioChatError):
    """设备未找到异常。

    主要功能：当 typed device API 无法为当前用户找到支持指定能力的在线设备时抛出。
    """


class AmbiguousDeviceError(AudioChatError):
    """设备选择不唯一异常。

    主要功能：当创建输入流或远程长命令时匹配到多台设备，要求调用方补充 selector。
    """


class DeviceBusyError(AudioChatError):
    """设备忙异常。"""


class CapabilityNotSupportedError(AudioChatError):
    """设备能力不支持异常。"""


class StreamTimeoutError(AudioChatError):
    """数据流超时异常。"""


class CommandFailedError(AudioChatError):
    """设备命令失败异常。"""


class PlaybackRejectedError(AudioChatError):
    """播放请求被拒绝异常。"""


@dataclass(frozen=True)
class ToolResult:
    """Tool 执行结果。

    主要功能：作为 ToolGateway 返回给 Agent Core 的稳定结构，避免直接暴露任意异常。
    主要属性：`ok` 标识成功与否，`data/message/assets/artifacts/tasks/meta/error`
    是公开冻结字段。
    """

    ok: bool
    data: Any = None
    message: str = ""
    assets: list[AssetRef] | None = None
    artifacts: list[ArtifactRef] | None = None
    tasks: list[Any] | None = None
    meta: dict | None = None
    error: dict | None = None

    @classmethod
    def success(
        cls,
        data: Any = None,
        *,
        message: str = "",
        assets: list[AssetRef] | None = None,
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
            artifacts=artifacts or [],
            tasks=tasks or [],
            meta=meta or {},
            error=None,
        )

    @classmethod
    def failed(cls, error: AudioChatError) -> "ToolResult":
        """创建失败结果。

        主要逻辑：把 SDK 异常转成稳定错误字典。
        参数：`error` 为 `AudioChatError`。
        返回值：`ToolResult`。
        异常情况：无。
        """
        error_dict = error.to_dict()
        message = str(error_dict.get("message") or error)
        return cls(ok=False, message=message, assets=[], artifacts=[], tasks=[], meta={}, error=error_dict)

    @property
    def content(self) -> Any:
        """返回 Tool 结果主体数据。"""

        return self.data

    @property
    def metadata(self) -> dict:
        """返回 Tool 结果附加信息。"""

        return dict(self.meta or {})


class ToolError(AudioChatError):
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


@dataclass
class ToolContext:
    """Tool 执行上下文。

    主要功能：由 SDK 注入用户、会话、设备、输出和资产上下文；Tool 不自行构造。
    公开边界：普通业务 Tool 只能通过显式设备 API、输出 API 和资产 API 完成动作，
    不直接暴露 tasks、memory、skills、mcp 等内部服务入口。
    """

    user_id: str
    session_id: str
    devices: "ToolDeviceFacade"
    output: Any = None
    assets: Any = None
    metadata: dict = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


@dataclass
class SystemToolContext(ToolContext):
    """系统内置 Tool 执行上下文。

    主要功能：只给 SDK 自带的运行时 Tool 使用，让 task_runtime_manager、
    memory_search、read_skill、mcp_call 等工具通过可见 Tool 边界访问内部服务。
    普通业务 Tool 不会拿到这些属性，避免在 Tool 内绕过模型可见工具列表。
    """

    tasks: Any = None
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

    def __init__(self) -> None:
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
            "timeout_seconds": spec.timeout_seconds,
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
        task_engine: Any = None,
        memory_service: Any = None,
        skill_service: Any = None,
        mcp_gateway: Any = None,
    ) -> None:
        self.app = app
        self.task_engine = task_engine
        self.memory_service = memory_service
        self.skill_service = skill_service
        self.mcp_gateway = mcp_gateway

    def create(self, *, user_id: str, session_id: str, tool_name: str | None = None) -> ToolContext:
        context_cls = SystemToolContext if tool_name in SYSTEM_CONTEXT_TOOL_NAMES else ToolContext
        internal_devices = DeviceRuntime(user_id=user_id, app=self.app, allow_long_running=False)
        kwargs = {}
        if context_cls is SystemToolContext:
            kwargs.update(
                {
                    "tasks": self.task_engine,
                    "memory": self.memory_service,
                    "skills": self.skill_service,
                    "mcp": self.mcp_gateway,
                }
            )
        return context_cls(
            user_id=user_id,
            session_id=session_id,
            devices=ToolDeviceFacade(context=internal_devices),
            output=OutputFacade(user_id=user_id, app=self.app),
            assets=AssetFacade(user_id=user_id, app=self.app),
            **kwargs,
        )


class ToolExecutor:
    """Tool 执行器。

    主要功能：封装 Tool.run 调用和错误转换，后续可接入超时、并发和取消策略。
    """

    async def execute(self, tool: BaseTool, context: ToolContext, input_data: dict) -> ToolResult:
        try:
            validated_input = self._validate_input(tool, input_data)
            coroutine = tool.run(context, validated_input)
            timeout_seconds = tool.resolved_spec().timeout_seconds
            if timeout_seconds:
                return await asyncio.wait_for(coroutine, timeout=timeout_seconds)
            return await coroutine
        except ValidationError as exc:
            return ToolResult.failed(
                ToolError(
                    "tool input validation failed",
                    code=ErrorCode.INVALID_ARGUMENT,
                    details={"errors": exc.errors()},
                )
            )
        except TimeoutError:
            return ToolResult.failed(ToolError("tool execution timeout", code=ErrorCode.TIMEOUT))
        except AudioChatError as exc:
            return ToolResult.failed(exc)
        except Exception as exc:
            return ToolResult.failed(ToolError(str(exc), code=ErrorCode.UNKNOWN))

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
    ) -> None:
        self.registry = registry or ToolRegistry()
        self.policy = policy or ToolPolicy()
        self.schema_builder = schema_builder or ToolSchemaBuilder()
        self.executor = executor or ToolExecutor()
        self.context_factory = context_factory
        self.recorder = recorder
        self.skill_service = skill_service
        self._progress_emitted: set[tuple[str, str]] = set()

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

        主要逻辑：调用 Stream Service 发布 `stream.output.close.requested`。
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
        self._history: dict[str, list[CommandEvent]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[CommandEvent]]] = {}
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
        self._command_devices[command_id] = device_set
        self._command_metadata[command_id] = dict(metadata or {})
        self._terminal_devices.setdefault(command_id, set())
        for device_id in device_set:
            self._device_commands.setdefault(device_id, set()).add(command_id)

    def metadata_for(self, command_id: str) -> dict[str, Any]:
        """返回命令登记时保存的 task 等元数据。"""

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
        for command_id in list(self._device_commands.get(device_id, set())):
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

        self._history.setdefault(command_event.command_id, []).append(command_event)
        for queue in list(self._subscribers.get(command_event.command_id, [])):
            queue.put_nowait(command_event)

    def _mark_terminal(self, command_id: str, device_id: str) -> None:
        """标记某台设备上的 command 已进入终态。"""

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

        seen = list(self._history.get(command_id, []))
        if _command_terminal_device_ids(seen) >= set(expected_device_ids):
            return seen
        queue: asyncio.Queue[CommandEvent] = asyncio.Queue()
        self._subscribers.setdefault(command_id, []).append(queue)
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
            subscribers = self._subscribers.get(command_id, [])
            if queue in subscribers:
                subscribers.remove(queue)
        return seen

    def subscribe(self, command_id: str) -> AsyncIterator[CommandEvent]:
        """订阅某个命令的真实端侧回执。"""

        async def _events() -> AsyncIterator[CommandEvent]:
            for event in self._history.get(command_id, []):
                yield event
            queue: asyncio.Queue[CommandEvent] = asyncio.Queue()
            self._subscribers.setdefault(command_id, []).append(queue)
            try:
                while True:
                    event = await queue.get()
                    yield event
                    if event.state in self.TERMINAL_STATES:
                        break
            finally:
                subscribers = self._subscribers.get(command_id, [])
                if queue in subscribers:
                    subscribers.remove(queue)

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
            raise AudioChatError(
                f"streaming sensor API is only available in TaskContext: {self.stream_type}",
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
            raise AudioChatError("streaming actuator API is only available in TaskContext", code=ErrorCode.PERMISSION_DENIED)
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
            raise AudioChatError("long running commands are only available in TaskContext", code=ErrorCode.PERMISSION_DENIED)
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

class TaskDeviceFacade(ToolDeviceFacade):
    """Task 可见设备能力门面。

    主要功能：在 ToolDeviceFacade 的短生命周期能力上，额外开放持续 stream 和远程
    长命令能力。
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
    defaults = properties.get("audio_chat.support_defaults") or {}
    if not isinstance(defaults, dict):
        return {}
    value = defaults.get(str(capability)) or {}
    return dict(value) if isinstance(value, dict) else {}


class DeviceRuntime:
    """SDK 内部设备运行时上下文。

    主要功能：承接 typed facade 的设备解析、事件投递、stream 打开和资产查询；
    不作为公开 API 导出，业务代码只能看到 `ToolDeviceFacade` 或 `TaskDeviceFacade`。
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
                raise AudioChatError(
                    f"selector must not use {key}",
                    code=ErrorCode.INVALID_ARGUMENT,
                    details={"field": key},
                )
            if key == "capability":
                support_ids = properties.get("audio_chat.support_ids") or []
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


class TaskRuntimeManagerTool(BaseTool):
    """统一管理 TaskEngine 运行实例的内置 Tool。

    主要功能：以一个模型可见工具承载 Task 的查询、取消和列表能力。
    业务 Task 启动由 SDK 自动生成的专用 start_* Tool 暴露给模型。
    """

    class Input(BaseModel):
        action: Literal["query", "cancel", "list_types", "list_instances"] = Field(
            description="任务管理动作：query 查询，cancel 取消，list_types 列出类型，list_instances 列出实例。启动任务请调用对应 start_* Tool。",
        )
        task_id: str | None = Field(default=None, description="查询或取消任务时使用的任务编号。")
        include_terminal: bool = Field(default=True, description="列出任务实例时是否包含已完成、取消、失败或超时任务。")

    spec = ToolSpec(
        name="task_runtime_manager",
        description=(
            "统一管理后台 Task 运行实例。只用于查询、取消或列出 Task；"
            "启动任务必须调用具体的 start_* Tool，例如 start_timer_task。"
        ),
        input_model=Input,
        capability_type="task",
        tags=["task", "manage"],
        progress_message=(
            "我处理一下这个后台任务。",
            "稍等，我看一下任务。",
        ),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行统一 Task 管理动作。

        参数：`action` 指定管理动作；`task_type/task_id/input_data` 是各动作所需参数。
        返回值：成功时返回任务类型、任务引用或实例列表。
        异常情况：TaskEngine 未配置、缺少必要参数或任务不存在时返回结构化失败。
        """

        if not isinstance(context, SystemToolContext) or context.tasks is None:
            return ToolResult.failed(ToolError("task engine is not configured", code=ErrorCode.PROTOCOL_ERROR))
        action = str(input_data.get("action") or "").strip()
        if action == "list_types":
            return ToolResult.success(data={"task_types": context.tasks.list_task_types()}, message="task types listed")
        if action == "list_instances":
            refs = context.tasks.list_tasks(
                user_id=context.user_id,
                include_terminal=bool(input_data.get("include_terminal", True)),
            )
            return ToolResult.success(
                data={"tasks": [ref.__dict__ for ref in refs]},
                tasks=refs,
                message=f"{len(refs)} tasks listed",
            )
        if action == "query":
            task_id = str(input_data.get("task_id") or "").strip()
            if not task_id:
                return ToolResult.failed(ToolError("task_id is required", code=ErrorCode.INVALID_ARGUMENT))
            ref = context.tasks.query(task_id)
            return ToolResult.success(data=ref.__dict__, tasks=[ref], message=ref.state)
        if action == "cancel":
            task_id = str(input_data.get("task_id") or "").strip()
            if not task_id:
                return ToolResult.failed(ToolError("task_id is required", code=ErrorCode.INVALID_ARGUMENT))
            ref = await context.tasks.cancel(task_id, reason="tool_requested")
            return ToolResult.success(data=ref.__dict__, tasks=[ref], message=ref.state)
        if action == "start":
            task_type = str(input_data.get("task_type") or "").strip()
            if not task_type:
                return ToolResult.failed(ToolError("task_type is required", code=ErrorCode.INVALID_ARGUMENT))
            start_input = _normalize_task_runtime_start_input(
                task_type=task_type,
                input_data=dict(input_data.get("input_data") or {}),
                available_task_types=_task_runtime_task_type_names(context.tasks.list_task_types()),
            )
            try:
                ref = await context.tasks.create(
                    task_type=start_input["task_type"],
                    user_id=context.user_id,
                    session_id=context.session_id,
                    input_data=start_input["input_data"],
                    summary=str(input_data.get("summary") or ""),
                )
            except AudioChatError as exc:
                error = exc.to_dict()
                requested_task_type = task_type
                resolved_task_type = str(start_input["task_type"])
                message = f"任务启动失败：{error.get('message') or str(exc)}"
                return ToolResult(
                    ok=False,
                    message=message,
                    assets=[],
                    artifacts=[],
                    tasks=[],
                    meta={
                        "operation": "task_start",
                        "requested_task_type": requested_task_type,
                        "resolved_task_type": resolved_task_type,
                    },
                    error=error,
                )
            return ToolResult.success(data=ref.__dict__, tasks=[ref], message=ref.state)
        return ToolResult.failed(ToolError(f"unknown task action: {action}", code=ErrorCode.INVALID_ARGUMENT))


class TaskStartTool(BaseTool):
    """由 SDK 根据 BaseTask 自动生成的模型可见启动 Tool。"""

    def __init__(
        self,
        *,
        task_type: str,
        description: str,
        input_model: Any = dict,
        tool_name: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.task_type = task_type
        self.name = tool_name or _default_task_start_tool_name(task_type)
        task_description = str(description or f"启动 {task_type} 后台任务。").strip()
        self.description = task_description
        self.input_model = input_model
        self.timeout_seconds = timeout_seconds
        self.spec = ToolSpec(
            name=self.name,
            description=_task_start_tool_description(task_type=task_type, description=task_description),
            input_model=input_model,
            capability_type="task",
            tags=["task", "start", task_type],
            timeout_seconds=timeout_seconds,
        )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if not isinstance(context, SystemToolContext) or context.tasks is None:
            return ToolResult.failed(ToolError("task engine is not configured", code=ErrorCode.PROTOCOL_ERROR))
        try:
            ref = await context.tasks.create(
                task_type=self.task_type,
                user_id=context.user_id,
                session_id=context.session_id,
                input_data=dict(input_data or {}),
                summary=str(self.description or self.task_type),
            )
        except AudioChatError as exc:
            error = exc.to_dict()
            return ToolResult(
                ok=False,
                message=f"任务启动失败：{error.get('message') or str(exc)}",
                assets=[],
                artifacts=[],
                tasks=[],
                meta={
                    "operation": "task_start",
                    "requested_task_type": self.task_type,
                    "resolved_task_type": self.task_type,
                    "start_tool_name": self.name,
                },
                error=error,
            )
        return ToolResult.success(
            data=ref.__dict__,
            tasks=[ref],
            message=f"{self.task_type} started",
            meta={"operation": "task_start", "start_tool_name": self.name},
        )


def _default_task_start_tool_name(task_type: str) -> str:
    normalized = str(task_type or "").strip()
    if normalized.endswith("_task"):
        return f"start_{normalized}"
    return f"start_{normalized}_task"


def _task_start_tool_description(*, task_type: str, description: str) -> str:
    task = str(task_type or "task").strip()
    summary = str(description or f"启动 {task} 后台任务。").strip().rstrip("。")
    return (
        f"{summary}。"
        f"当用户明确要求启动或持续执行该后台能力时调用本工具；"
        f"调用成功后 SDK 会创建 `{task}` 后台 Task 实例并立即返回 task_id。"
        "Task 会在后台继续运行、发出状态信号或到点通知；"
        "后续查询进度、列出任务或取消任务请使用 task_runtime_manager。"
    )


def _task_runtime_task_type_names(rows: Any) -> set[str]:
    """Extract registered task type names from TaskEngine list output."""

    names: set[str] = set()
    for row in rows or []:
        if isinstance(row, dict):
            value = row.get("task_type")
        else:
            value = row
        text = str(value or "").strip()
        if text:
            names.add(text)
    return names


def _normalize_task_runtime_start_input(
    *,
    task_type: str,
    input_data: dict,
    available_task_types: set[str],
) -> dict[str, Any]:
    """Normalize common model aliases for built-in task starts."""

    normalized_type = task_type
    normalized_input = dict(input_data)
    if task_type in {"timer", "timer_task", "reminder", "alarm", "countdown", "计时器", "倒计时", "提醒", "闹钟"}:
        if "timer_task" in available_task_types:
            normalized_type = "timer_task"
            normalized_input = _normalize_timer_task_input(normalized_input)
    return {"task_type": normalized_type, "input_data": normalized_input}


def _normalize_timer_task_input(input_data: dict) -> dict:
    """Map provider-friendly timer/reminder fields to the sample timer_task contract."""

    normalized = dict(input_data)
    seconds = normalized.get("seconds")
    if seconds is None:
        seconds = normalized.get("duration_seconds")
    if seconds is None:
        seconds = normalized.get("delay_seconds")
    if seconds is None:
        seconds = normalized.get("timeout_seconds")
    if seconds is not None:
        normalized["seconds"] = int(float(seconds))
    message = str(normalized.get("message") or normalized.get("notify_text") or normalized.get("text") or "").strip()
    if message:
        normalized["message"] = message
        normalized["notify_text"] = message
    normalized.setdefault("auto_fire", True)
    return normalized


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
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.mcp is None:
            return ToolResult.failed(ToolError("mcp gateway is not configured", code=ErrorCode.PROTOCOL_ERROR))
        result = context.mcp.call(
            tool_name=str(input_data.get("tool_name") or ""),
            arguments=dict(input_data.get("arguments") or {}),
            timeout_seconds=input_data.get("timeout_seconds"),
        )
        return ToolResult.success(data=result, message="mcp tool called")


BUILTIN_TOOLS = (
    QueryDeviceStateTool,
    TaskRuntimeManagerTool,
)

EXTENSION_BUILTIN_TOOLS = (
    MemorySearchTool,
    ManageMemoryTool,
    ReadSkillTool,
    McpCallTool,
)

SYSTEM_CONTEXT_TOOL_NAMES = {
    TaskRuntimeManagerTool.spec.name,
    MemorySearchTool.spec.name,
    ManageMemoryTool.spec.name,
    ReadSkillTool.spec.name,
    McpCallTool.spec.name,
}
