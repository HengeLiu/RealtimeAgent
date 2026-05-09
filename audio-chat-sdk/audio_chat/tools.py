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
    """设备忙异常。当前版本先作为公开错误类型预留。"""


class CapabilityNotSupportedError(AudioChatError):
    """设备能力不支持异常。当前版本先作为公开错误类型预留。"""


class StreamTimeoutError(AudioChatError):
    """数据流超时异常。当前版本先作为公开错误类型预留。"""


class CommandFailedError(AudioChatError):
    """设备命令失败异常。当前版本先作为公开错误类型预留。"""


class PlaybackRejectedError(AudioChatError):
    """播放请求被拒绝异常。当前版本先作为公开错误类型预留。"""


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
        return cls(ok=False, assets=[], artifacts=[], tasks=[], meta={}, error=error.to_dict())

    @property
    def content(self) -> Any:
        """历史测试和旧调用方读取的 `content` 字段。"""

        return self.data

    @property
    def metadata(self) -> dict:
        """历史测试和旧调用方读取的 `metadata` 字段。"""

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

    主要功能：只给 SDK 自带的专用 Tool 使用，让 query_task_status、
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

        主要逻辑：优先使用开发者声明的 `spec = ToolSpec(...)`；没有 spec 时继续
        兼容 `name/description/input_model` 简写。
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
        类型转换和必填校验；为了兼容现有工具，传给 run 的仍是 dict。
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
    subscriptions: list[dict] = field(default_factory=list)


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

    主要功能：描述 `CommandHandle.results()` 产生的远程命令状态。当前版本先提供
    结构和最小 completed 事件，后续再接入真实设备回报订阅。
    """

    command_id: str
    name: str
    state: str
    data: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class DeviceLease:
    """设备能力选择结果。

    主要功能：作为 SDK 内部 selector 解析结果，封装能力、selector、设备集合和
    默认参数，避免 typed API 后续步骤重新选择设备。
    """

    capability: str | None
    selector: dict
    devices: tuple[Any, ...]
    params: dict = field(default_factory=dict)


class CommandHandle:
    """设备长命令句柄。

    主要功能：给 Task 维护远程设备任务状态提供稳定对象。当前过渡说明会先返回一次
    accepted/running 事件，并在 `stop()` 时发送停止命令。
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

        async for event in self._context.commands.subscribe_result(self.command_id):
            yield event
            if event.state in {"completed", "failed"}:
                break

    async def stop(self, *, reason: str = "task_cancelled") -> CommandResult:
        """停止远程命令。

        主要逻辑：发送 `*.stop` 约定命令，端侧可按 command_id 释放资源。
        """

        self._stopped = True
        return await self._context.commands.call(
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

    def record(self, event: Event) -> None:
        """记录端侧命令回执并唤醒订阅者。"""

        payload = dict(event.payload or {})
        command_id = str(payload.get("command_id") or "").strip()
        if not command_id:
            return
        state = str(event.event_name).rsplit(".", 1)[-1]
        command_event = CommandEvent(
            command_id=command_id,
            name=str(payload.get("command") or payload.get("command_name") or ""),
            state=state,
            data={"producer_id": event.producer_id, **payload},
        )
        self._history.setdefault(command_id, []).append(command_event)
        for queue in list(self._subscribers.get(command_id, [])):
            queue.put_nowait(command_event)

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
            configure_payload=payload,
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
                f"streaming sensor API is only available in DeviceContext: {self.stream_type}",
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
            raise AudioChatError("streaming actuator API is only available in DeviceContext", code=ErrorCode.PERMISSION_DENIED)
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
        result = self._context._publish_event_to_devices(
            event_name=EventName.COMMAND_REQUESTED,
            payload={"command_id": command_id, "command": name, **dict(params or {})},
            devices=devices,
        )
        self._context._record_command_trace(
            {
                "event": "command.call.requested",
                "command_id": command_id,
                "command_name": name,
                "selector": dict(selector or {}),
                "device_count": len(devices),
                "delivered_count": result.delivered_count,
            }
        )
        expected_device_ids = tuple(device_id for device_id in result.matched_device_ids if device_id not in result.failed_device_ids)
        events = await self._context._command_result_broker().wait(
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
                "command_name": name,
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
            raise AudioChatError("long running commands are only available in DeviceContext", code=ErrorCode.PERMISSION_DENIED)
        devices = self._context._resolve_devices_for_command(selector=selector, require_single=True)
        command_id = new_id("cmd")
        self._context._publish_event_to_devices(
            event_name=EventName.COMMAND_REQUESTED,
            payload={"command_id": command_id, "command": name, "mode": "start", **dict(params or {})},
            devices=devices,
        )
        self._context._record_command_trace(
            {
                "event": "command.start.requested",
                "command_id": command_id,
                "command_name": name,
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

        return self._context.get_devices()

    def _publish_control_event(
        self,
        event_name: str | EventName,
        *,
        stream_type: str | StreamType | None = None,
        payload: dict | None = None,
        selector: dict | None = None,
        selection: str = "first_available",
    ) -> PublishResult:
        """供 SDK 内置系统工具发布协议事件，普通业务代码不应依赖。"""

        devices = self._context._resolve_devices_for_capability(stream_type, selector=selector, require_single=False)
        if selection == "first_available":
            devices = devices[:1]
        return self._context._publish_event_to_devices(
            event_name=event_name,
            stream_type=stream_type,
            payload=payload,
            devices=devices,
        )


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
    主要方法：`publish_event()`、`open_output_stream()`、`request_asset()`、
    `query_assets()`、`watch_assets()`、`submit_text()`、`submit_audio()`。
    """

    def __init__(self, *, user_id: str, app, allow_long_running: bool = False) -> None:
        self.user_id = user_id
        self._app = app
        self._allow_long_running = allow_long_running
        self.sensors = _SensorsFacade(context=self, allow_stream=allow_long_running)
        self.actuators = _ActuatorsFacade(context=self, allow_stream=allow_long_running)
        self.commands = _CommandsFacade(context=self, allow_long_running=allow_long_running)

    def get_devices(self) -> list[DeviceSnapshot]:
        """返回当前用户 active device set 的只读快照。

        主要逻辑：从 Control Service 读取在线设备，返回身份、名称、properties 和
        subscriptions，供 Tool / Task 做状态说明或调试展示。通讯仍然必须走事件和 stream。
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
                    subscriptions=[subscription.__dict__ for subscription in getattr(record, "subscriptions", [])],
                )
            )
        return devices

    def publish_event(
        self,
        event_name: str | EventName,
        payload: dict | None = None,
        stream_type: str | StreamType | None = None,
        selection: str = "all",
        timeout_seconds: float | None = None,
    ) -> PublishResult:
        """发布协议控制事件。

        主要逻辑：事件按 user_id、订阅、stream_type 和 selection 匹配端侧；
        业务代码不能指定 device_id。
        参数：`event_name` 为协议事件名，`payload` 为事件载荷，`stream_type` 为可选
        stream 过滤，`selection` 为选择策略，`timeout_seconds` 预留给未来 ACK 等待，
        本阶段不阻塞。
        返回值：`PublishResult`。
        异常情况：事件非法或 selection 非法时抛出 `ValueError`。
        """
        _ = timeout_seconds
        event = Event(
            event_name=event_name,
            user_id=self.user_id,
            producer_id=SERVER_PRODUCER_ID,
            stream_type=stream_type,
            payload=dict(payload or {}),
        )
        return self._app.control_service.publish_matching(
            event,
            selection=selection,
        )

    def open_output_stream(
        self,
        stream_type: str,
        codec: str,
        metadata: dict | None = None,
        selection: str = "all",
    ) -> OutputStreamWriter:
        """打开 output stream 并返回写入器。

        主要逻辑：创建 `actuator.*` stream，底层按订阅和 selection 选出消费端，
        并把后续 chunk 固定投递到这批设备。
        参数：`stream_type` 为 output stream 类型，`codec` 为编码，`metadata` 为可选
        元信息，`selection` 为设备匹配策略。
        返回值：`OutputStreamWriter`。
        异常情况：stream 类型或格式非法时由 Stream Service 抛出异常。
        """
        _ = metadata
        session_id = self._app.active_session_id(self.user_id)
        format = StreamFormat(codec=codec)
        handle = self._app.stream_service.open_stream(
            user_id=self.user_id,
            session_id=session_id,
            stream_type=stream_type,
            producer_id=SERVER_PRODUCER_ID,
            format=format,
            stream_id=new_id("stream_out"),
            selection=selection,
        )
        return OutputStreamWriter(context=self, stream_id=handle.stream_id, session_id=session_id, stream_type=stream_type, format=format)

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

    def capture_photo(
        self,
        *,
        reason: str = "",
        timeout_seconds: float | None = None,
        freshness_seconds: float = 0,
        configure_payload: dict | None = None,
    ) -> AssetRef | None:
        """请求端侧通过 `sensor.rgb` stream 采集一张图片。

        主要逻辑：这是历史版本 `capture_photo()` 的迁移便捷入口，底层仍然调用
        `request_asset("sensor.rgb")`，由控制事件触发端侧通过 stream 上传 JPEG。
        参数：`reason` 为抓拍原因，`timeout_seconds` 为等待超时，`freshness_seconds`
        为允许复用缓存的最大秒数，`configure_payload` 为端侧补充配置。
        返回值：命中或采集成功时返回 `AssetRef`，超时时返回 `None`。
        异常情况：端侧配置 payload 包含媒体字节或控制事件非法时抛出异常。
        """

        payload = {"reason": reason} if reason else {}
        payload.update(dict(configure_payload or {}))
        return self.request_asset(
            stream_type="sensor.rgb",
            freshness_seconds=freshness_seconds,
            configure_payload=payload,
            timeout_seconds=timeout_seconds,
        )

    def latest_asset(self, stream_type: str | StreamType, *, freshness_seconds: float | None = None) -> AssetRef | None:
        """读取指定 stream 类型的最新内部缓存引用。

        主要逻辑：只查询 server 对 sensor stream 结果的内部缓存，不主动发布控制事件。
        参数：`stream_type` 为 sensor stream 类型，`freshness_seconds` 为可选最大年龄。
        返回值：存在时返回最新 `AssetRef`，否则返回 `None`。
        异常情况：无。
        """

        refs = self.query_assets(stream_type=stream_type, freshness_seconds=freshness_seconds)
        return refs[-1] if refs else None

    def request_asset(
        self,
        stream_type: str | StreamType,
        freshness_seconds: float = 0,
        configure_payload: dict | None = None,
        timeout_seconds: float | None = None,
    ) -> AssetRef | None:
        """请求单个传感器 stream 的内部缓存引用。

        主要逻辑：委托内部缓存服务先查 freshness 缓存，未命中时发布
        `stream.control.open.requested`，等待端侧通过 `sensor.*` stream 上传。
        参数：`stream_type` 为 sensor stream，`freshness_seconds` 为缓存新鲜度，
        `configure_payload` 为配置载荷，`timeout_seconds` 为等待超时。
        返回值：`AssetRef` 或 `None`。
        异常情况：底层事件发布或文件存储失败时向上抛出。
        """
        return self._app.asset_service.request_asset(
            user_id=self.user_id,
            stream_type=stream_type,
            freshness_seconds=freshness_seconds,
            configure_payload=configure_payload,
            session_id=self._app.active_session_id(self.user_id),
            timeout_seconds=timeout_seconds,
        )

    def _request_asset_for_devices(
        self,
        *,
        stream_type: str | StreamType,
        devices: list[Any],
        freshness_seconds: float = 0,
        configure_payload: dict | None = None,
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
            configure_payload=configure_payload,
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

    def query_assets(self, stream_type: str | StreamType, freshness_seconds: float | None = None) -> list[AssetRef]:
        """查询 sensor stream 结果缓存窗口。

        主要逻辑：读取 server 对 sensor stream 结果的缓存窗口，并按 freshness_seconds 可选过滤。
        参数：`stream_type` 为 sensor stream，`freshness_seconds` 为最大年龄。
        返回值：`AssetRef` 列表。
        异常情况：无。
        """
        return self._app.asset_service.query_assets(
            user_id=self.user_id,
            stream_type=stream_type,
            freshness_seconds=freshness_seconds,
        )

    def watch_assets(
        self,
        stream_type: str | StreamType,
        correlation_id: str | None = None,
        timeout_seconds: float | None = None,
        since: float | str | None = None,
    ) -> AsyncIterator[AssetRef]:
        """持续读取 sensor stream 写入后的内部缓存引用。

        主要逻辑：返回 Asset Service 的 async iterator，按 stream_type 和 correlation_id
        过滤，适合 Task 消费连续 sensor.rgb 帧。
        参数：`stream_type` 为资产 stream，`correlation_id` 为可选任务关联 ID，
        `timeout_seconds` 为无新资产时的退出时间。
        返回值：异步迭代器。
        异常情况：无。
        """
        return self._watch_assets_filtered(
            stream_type=stream_type,
            correlation_id=correlation_id,
            timeout_seconds=timeout_seconds,
            since=since,
        )

    def submit_text(self, text: str, priority: str = "normal", ttl_seconds: int = 0) -> None:
        """提交文本输出。

        主要逻辑：业务只提交文本和优先级，TTS、播放仲裁和 speaker stream 由 Output
        Service 负责。
        参数：`text` 为要播报的文本，`priority` 为播放优先级，`ttl_seconds` 为排队 TTL。
        返回值：无。
        异常情况：Output Service 写入失败时向上抛出。
        """
        session_id = self._app.active_session_id(self.user_id)
        self._app.output_service.submit_text(
            user_id=self.user_id,
            session_id=session_id,
            text=text,
            priority=priority,
            ttl_seconds=ttl_seconds,
        )

    def notify(self, text: str, *, priority: str = "normal", ttl_seconds: int = 0) -> None:
        """提交用户可感知通知。

        主要逻辑：作为历史版本 `submit_notification()` 的迁移便捷入口，底层进入
        Output Service，由 TTS、播放仲裁和输出 stream 统一处理。
        参数：`text` 为通知文本，`priority` 为优先级，`ttl_seconds` 为通知有效期。
        返回值：无。
        异常情况：同 `submit_text()`。
        """

        self.submit_text(text=text, priority=priority, ttl_seconds=ttl_seconds)

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
        `stream.control.open.requested` 和 `watch_assets()`。
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
            async for asset in self.watch_assets(
                stream_type,
                correlation_id=correlation_id,
                timeout_seconds=timeout_seconds if timeout_seconds is not None else duration_seconds,
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
        if capability is not None and not candidates and active_devices:
            raise CapabilityNotSupportedError(
                f"no online device supports capability: {capability}",
                code=ErrorCode.NOT_FOUND,
                details={"capability": capability, "selector": selector or {}},
            )
        devices = [device for device in candidates if self._selector_matches(device, selector or {})]
        if not devices:
            raise DeviceNotFoundError(
                f"no online device matches capability: {capability or 'command'}",
                code=ErrorCode.NOT_FOUND,
                details={"capability": capability, "selector": selector or {}},
            )
        if require_single and len(devices) > 1:
            raise AmbiguousDeviceError(
                f"multiple devices match capability: {capability or 'command'}",
                code=ErrorCode.INVALID_ARGUMENT,
                details={"capability": capability, "selector": selector or {}, "matched_count": len(devices)},
            )
        lease = DeviceLease(
            capability=capability,
            selector=dict(selector or {}),
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
            if key == "device_id":
                raise AudioChatError("selector must not use device_id", code=ErrorCode.INVALID_ARGUMENT)
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

    def submit_audio(self, audio: bytes, codec: str, priority: str = "normal") -> None:
        """提交原生音频输出。

        主要逻辑：音频不经过 TTS，直接走 Output Service 原生 audio 入口和播放仲裁。
        参数：`audio` 为二进制音频，`codec` 为编码，`priority` 为播放优先级。
        返回值：无。
        异常情况：Output Service 写入失败时向上抛出。
        """
        session_id = self._app.active_session_id(self.user_id)
        self._app.output_service.submit_audio(
            user_id=self.user_id,
            session_id=session_id,
            audio=audio,
            format=StreamFormat(codec=codec),
            priority=priority,
        )

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


class RequestAssetInput(BaseModel):
    """请求传感器资产输入。"""

    stream_type: str = Field(description="要获取的传感器数据类型，例如 sensor.rgb。")
    freshness_seconds: float = Field(default=0, ge=0, description="允许复用缓存资产的最大秒数；0 表示必须请求新资产。")
    configure_payload: dict = Field(default_factory=dict, description="可选采集参数，不要放入媒体字节。")
    timeout_seconds: float | None = Field(default=None, gt=0, description="等待资产返回的超时时间，单位秒。")


class RequestAssetOutput(BaseModel):
    """请求传感器资产输出。"""

    asset_id: str | None = Field(default=None, description="资产 ID；超时或不可用时为空。")
    stream_type: str | None = Field(default=None, description="资产来源类型。")
    uri: str | None = Field(default=None, description="资产 URI。")
    mime_type: str | None = Field(default=None, description="资产 MIME 类型。")
    size_bytes: int | None = Field(default=None, description="资产字节数。")
    created_at_ms: int | None = Field(default=None, description="资产生成时间戳，毫秒。")


class RequestAssetTool(BaseTool):
    """请求对话资产的 typed Tool 门面。

    主要功能：让 Agent 工具调用通过 `context.devices.sensors.*.one()` 获取资产引用。
    主要方法：`run()`。
    """

    spec = ToolSpec(
        name="request_asset",
        description="获取指定类型的传感器资产。需要当前照片时优先使用 capture_photo。",
        input_model=RequestAssetInput,
        output_model=RequestAssetOutput,
        capability_type="tool",
        tags=["asset", "stream", "sensor"],
    )

    async def run(
        self,
        context: ToolContext,
        input_data: dict | None = None,
        **kwargs,
    ):
        """执行资产请求工具。

        主要逻辑：直接转调 typed sensor API。
        参数：`context` 为用户设备上下文，`stream_type` 为资产 stream，
        `freshness_seconds` 为缓存新鲜度，`configure_payload` 为端侧配置。
        返回值：`AssetRef` 或 `None`。
        异常情况：同 Context 方法。
        """
        _ = kwargs
        return await self._run_tool_context(context, dict(input_data or {}))

    async def _run_tool_context(self, context: ToolContext, input_data: dict) -> ToolResult:
        """按 ToolGateway 调用形态执行资产请求。"""

        stream_type = str(input_data.get("stream_type") or "")
        sensor = {
            "sensor.rgb": context.devices.sensors.rgb,
            "sensor.imu": context.devices.sensors.imu,
            "sensor.tof": context.devices.sensors.tof,
        }.get(stream_type)
        if sensor is None:
            return ToolResult.failed(
                ToolError(
                    f"unsupported asset stream_type: {stream_type}",
                    code=ErrorCode.INVALID_ARGUMENT,
                    details={"stream_type": stream_type},
                )
            )
        asset = await sensor.one(
            timeout_seconds=float(input_data.get("timeout_seconds") or 10),
            params=input_data.get("configure_payload"),
        )
        return ToolResult.success(
            data={
                "asset_id": asset.asset_id,
                "stream_type": asset.stream_type,
                "uri": asset.uri,
                "mime_type": asset.mime_type,
                "size_bytes": asset.size_bytes,
                "created_at_ms": asset.created_at_ms,
            },
            assets=[asset],
            message="asset requested",
        )


class CapturePhotoInput(BaseModel):
    """抓拍图片输入。"""

    reason: str = Field(default="agent_requested", description="抓拍原因，用简短中文说明即可。")
    timeout_seconds: float | None = Field(default=10, description="等待图片返回的超时时间，单位秒。")
    freshness_seconds: float = Field(default=0, description="可复用缓存图片的最大秒数；需要新图时填 0。")


class CapturePhotoOutput(BaseModel):
    """抓拍图片输出。"""

    asset_id: str
    stream_type: str
    uri: str
    mime_type: str
    size_bytes: int | None = None
    created_at_ms: int
    metadata: dict = Field(default_factory=dict)


class CapturePhotoTool(BaseTool):
    """内置抓拍 Tool。

    主要功能：让模型继续用老工具名请求当前视觉资产；实现上只请求 `sensor.rgb`
    stream，不引入相机 RPC 或点对点 device_id。
    """

    spec = ToolSpec(
        name="capture_photo",
        description=(
            "当用户询问眼前画面、物体、文字、障碍物、路况等需要新的视觉信息才能回答的问题时调用。"
            "普通闲聊、记忆维护或已有当前照片足够回答时不要调用。"
        ),
        input_model=CapturePhotoInput,
        output_model=CapturePhotoOutput,
        capability_type="tool",
        tags=["camera", "image", "system"],
        progress_message=(
            "我先拍张照片看看。",
            "稍等，我看一下眼前画面。",
            "我先取一张当前画面。",
        ),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """请求一张 `sensor.rgb` 图片资产。

        主要逻辑：通过 typed device facade 发布 stream open 事件并等待
        端侧上传图片；返回资产引用给 Agent Core。
        参数：`input_data` 可包含 reason、timeout_seconds、freshness_seconds。
        返回值：成功时包含 AssetRef，超时时返回结构化失败。
        异常情况：无匹配设备或等待超时会转为 `TIMEOUT` 失败。
        """

        asset = await context.devices.sensors.rgb.one(
            timeout_seconds=float(input_data.get("timeout_seconds") or 10),
            params={"reason": str(input_data.get("reason") or "agent_requested")},
        )
        return ToolResult.success(
            data={
                "asset_id": asset.asset_id,
                "stream_type": asset.stream_type,
                "uri": asset.uri,
                "mime_type": asset.mime_type,
                "size_bytes": asset.size_bytes,
                "created_at_ms": asset.created_at_ms,
                "metadata": dict(asset.metadata),
            },
            assets=[asset],
            message="已完成一次抓拍。",
        )


class PublishDeviceCommandTool(BaseTool):
    """发布端侧命令事件的内置 Tool。"""

    class Input(BaseModel):
        command_name: str = Field(description="命令名称，例如 actuator.haptic.pulse 或 phone.task.start。")
        params: dict = Field(default_factory=dict, description="命令参数，只放小型结构化数据。")
        selection: Literal["first_available", "all"] = Field(default="first_available", description="使用第一台可用设备，或所有可用设备。")

    class Output(BaseModel):
        matched_count: int = Field(description="订阅匹配并经过选择策略后的设备数量。")
        delivered_count: int = Field(description="实际投递成功的设备数量。")

    spec = ToolSpec(
        name="publish_device_command",
        description="让可用设备执行一个轻量命令，例如震动、提示音或启动设备侧动作。",
        input_model=Input,
        output_model=Output,
        capability_type="tool",
        tags=["device", "control"],
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        result = await context.devices.commands.call(
            name=str(input_data.get("command_name") or ""),
            params=dict(input_data.get("params") or {}),
            require_single=str(input_data.get("selection") or "first_available") == "first_available",
        )
        return ToolResult.success(data=result.__dict__, message="device command completed" if result.ok else "device command failed")


class StartPhoneVideoLinkInput(BaseModel):
    """启动视频画面连接输入。"""

    link_mode: str = Field(default="direct", description="视频连接方式；通常保持默认值。")
    frame_interval_ms: int = Field(default=500, ge=1, description="期望画面间隔，单位毫秒；越小越实时。")
    duration_seconds: float | None = Field(default=None, description="可选持续时间，单位秒。")


class StartPhoneVideoLinkOutput(BaseModel):
    """启动视频画面连接输出。"""

    link_id: str
    task_id: str
    task_type: str
    state: str
    stream_type: str
    delivered_count: int
    frame_interval_ms: int


class StartPhoneVideoLinkTool(BaseTool):
    """启动 RGB 连续画面流的系统 Tool。

    主要功能：请求可生产 `sensor.rgb` 的端侧建立连续 RGB stream。新版 SDK 不再暴露
    手机/眼镜点对点连接细节，因此本工具只返回配置事件投递结果和 link_id。
    """

    spec = ToolSpec(
        name="start_phone_video_link",
        description=(
            "当任务需要手机或其他摄像头持续提供画面时调用，例如持续观察、找物、导航或红绿灯辅助。"
            "只需要单张眼前照片时不要调用，应使用 capture_photo。"
        ),
        input_model=StartPhoneVideoLinkInput,
        output_model=StartPhoneVideoLinkOutput,
        capability_type="tool",
        tags=["phone", "video", "stream"],
        progress_message=(
            "我先连接手机摄像头。",
            "稍等，我把手机画面接进来。",
            "我先建立视频画面连接。",
        ),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """请求端侧开始连续上传 RGB 资产。

        主要逻辑：发布 `stream.control.open.requested`，由设备订阅策略决定具体
        端侧；不接受 device_id 参数。
        参数：`frame_interval_ms` 控制上传间隔，`duration_seconds` 控制端侧自行停止。
        返回值：配置事件投递摘要。
        异常情况：无匹配订阅设备时仍返回 delivered_count=0，供 Agent 解释。
        """

        frame_interval_ms = max(1, int(input_data.get("frame_interval_ms") or 500))
        link_id = new_id("video_link")
        payload = {
            "mode": "continuous",
            "link_id": link_id,
            "link_mode": str(input_data.get("link_mode") or "direct"),
            "frame_interval_ms": frame_interval_ms,
            "rate_hz": 1000.0 / frame_interval_ms,
            "correlation_id": link_id,
            "reason": "start_phone_video_link",
        }
        if input_data.get("duration_seconds") is not None:
            payload["duration_seconds"] = float(input_data["duration_seconds"])
        result = context.devices._publish_control_event(
            "stream.control.open.requested",
            stream_type="sensor.rgb",
            payload=payload,
        )
        state = "running" if result.delivered_count > 0 else "unavailable"
        return ToolResult.success(
            data={
                "link_id": link_id,
                "task_id": link_id,
                "task_type": "phone_video_link_task",
                "state": state,
                "stream_type": "sensor.rgb",
                "delivered_count": result.delivered_count,
                "frame_interval_ms": frame_interval_ms,
            },
            message="已请求建立视频画面连接。" if result.delivered_count else "没有找到可建立视频画面连接的设备。",
        )


class QueryDeviceStateTool(BaseTool):
    """查询当前用户 active device set 的内置 Tool。"""

    class Input(BaseModel):
        include_subscriptions: bool = Field(default=True, description="是否返回设备可接收的事件摘要。")

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
        include_subscriptions = bool(input_data.get("include_subscriptions", True))
        rows = []
        for device in devices:
            row = dict(device.__dict__)
            if not include_subscriptions:
                row.pop("subscriptions", None)
            rows.append(row)
        data = {"devices": rows, "count": len(devices)}
        return ToolResult.success(
            data=data,
            message=f"{len(devices)} active devices online",
        )


class QueryTaskStatusTool(BaseTool):
    """查询 TaskEngine 任务状态的内置 Tool。"""

    class Input(BaseModel):
        task_id: str = Field(description="要查询的任务编号；没有明确任务编号时不要猜测。")

    spec = ToolSpec(
        name="query_task_status",
        description="当用户询问已启动任务的进度、状态或结果时调用；只适用于已经有任务编号的任务。",
        input_model=Input,
        capability_type="tool",
        tags=["task"],
        progress_message=(
            "我查一下任务状态。",
            "稍等，我看一下任务进度。",
        ),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.tasks is None:
            return ToolResult.failed(ToolError("task engine is not configured", code=ErrorCode.PROTOCOL_ERROR))
        ref = context.tasks.query(str(input_data.get("task_id") or ""))
        return ToolResult.success(data=ref.__dict__, tasks=[ref], message=ref.state)


class CancelTaskTool(BaseTool):
    """取消 TaskEngine 任务的内置 Tool。"""

    class Input(BaseModel):
        task_id: str = Field(description="要取消的任务编号；没有明确任务编号时不要猜测。")
        reason: str = Field(default="tool_requested", description="取消原因。")

    spec = ToolSpec(
        name="cancel_task",
        description="当用户明确要求停止、取消或结束某个正在运行的任务时调用；只适用于已经有任务编号的任务。",
        input_model=Input,
        capability_type="tool",
        tags=["task"],
        progress_message=(
            "我帮你停止这个任务。",
            "好的，我正在取消任务。",
        ),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.tasks is None:
            return ToolResult.failed(ToolError("task engine is not configured", code=ErrorCode.PROTOCOL_ERROR))
        ref = await context.tasks.cancel(str(input_data.get("task_id") or ""), reason=str(input_data.get("reason") or "tool_requested"))
        return ToolResult.success(data=ref.__dict__, tasks=[ref], message=ref.state)


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
    """管理长期记忆的内置 Tool。"""

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
    RequestAssetTool,
    CapturePhotoTool,
    PublishDeviceCommandTool,
    StartPhoneVideoLinkTool,
    QueryDeviceStateTool,
    QueryTaskStatusTool,
    CancelTaskTool,
)

EXTENSION_BUILTIN_TOOLS = (
    MemorySearchTool,
    ManageMemoryTool,
    ReadSkillTool,
    McpCallTool,
)

SYSTEM_CONTEXT_TOOL_NAMES = {
    QueryTaskStatusTool.spec.name,
    CancelTaskTool.spec.name,
    MemorySearchTool.spec.name,
    ManageMemoryTool.spec.name,
    ReadSkillTool.spec.name,
    McpCallTool.spec.name,
}
