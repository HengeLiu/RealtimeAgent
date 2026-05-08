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
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamChunk, StreamFormat, new_id


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
        """兼容旧测试和旧调用方读取的 `content` 字段。"""

        return self.data

    @property
    def metadata(self) -> dict:
        """兼容旧测试和旧调用方读取的 `metadata` 字段。"""

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
    session_id: str
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

    主要功能：由 SDK 注入用户、会话、设备上下文和 Task Engine；Tool 不自行构造。
    主要属性：`devices` 是 `UserDeviceContext`，`tasks` 为可选 Task Engine。
    """

    user_id: str
    session_id: str
    devices: "UserDeviceContext"
    tasks: Any = None
    memory: Any = None
    skills: Any = None
    mcp: Any = None
    metadata: dict = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


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

    主要功能：集中创建 Tool 执行上下文，确保设备通讯能力只通过 UserDeviceContext 注入。
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

    def create(self, *, user_id: str, session_id: str) -> ToolContext:
        return ToolContext(
            user_id=user_id,
            session_id=session_id,
            devices=UserDeviceContext(user_id=user_id, app=self.app),
            tasks=self.task_engine,
            memory=self.memory_service,
            skills=self.skill_service,
            mcp=self.mcp_gateway,
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
            self.context_factory.create(user_id=user_id, session_id=session_id),
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

    def __init__(self, *, context: "UserDeviceContext", stream_id: str, session_id: str, stream_type: str, format: StreamFormat) -> None:
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


class UserDeviceContext:
    """业务代码访问用户端侧能力的协议原生上下文门面。

    主要功能：Tool / Task 只能通过事件、sensor stream、输出意图和 stream 写入器表达业务意图；
    不面向某个 device_id 编程，也不暴露端侧 RPC。Asset 是 server 内部对 sensor stream
    结果的缓存引用，保留在方法名中是为了兼容既有 Tool。
    主要方法：`publish_event()`、`open_output_stream()`、`request_asset()`、
    `query_assets()`、`watch_assets()`、`submit_text()`、`submit_audio()`。
    """

    def __init__(self, *, user_id: str, app) -> None:
        self.user_id = user_id
        self._app = app

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
        event_name: str,
        payload: dict | None = None,
        stream_type: str | None = None,
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

    def capture_photo(
        self,
        *,
        reason: str = "",
        timeout_seconds: float | None = None,
        freshness_seconds: float = 0,
        configure_payload: dict | None = None,
    ) -> AssetRef | None:
        """请求端侧通过 `sensor.rgb` stream 采集一张图片。

        主要逻辑：这是旧 SDK `capture_photo()` 的迁移便捷入口，底层仍然调用
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

    def latest_asset(self, stream_type: str, *, freshness_seconds: float | None = None) -> AssetRef | None:
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
        stream_type: str,
        freshness_seconds: float = 0,
        configure_payload: dict | None = None,
        timeout_seconds: float | None = None,
    ) -> AssetRef | None:
        """请求单个传感器 stream 的内部缓存引用。

        主要逻辑：委托内部缓存服务先查 freshness 缓存，未命中时发布
        `stream.control.configure.requested`，等待端侧通过 `sensor.*` stream 上传。
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

    def configure_stream(
        self,
        stream_type: str,
        *,
        mode: str,
        rate_hz: float | None = None,
        duration_seconds: float | None = None,
        payload: dict | None = None,
        selection: str = "first_available",
        timeout_seconds: float | None = None,
    ) -> PublishResult:
        """通过协议事件配置端侧 sensor stream。

        主要逻辑：发布 `stream.control.configure.requested`，让订阅命中的端侧自行
        打开、调整或停止对应 stream；不会新增隐藏 RPC。
        参数：`stream_type` 为 stream 类型，`mode` 为 single/continuous/stop 等模式，
        `rate_hz` 和 `duration_seconds` 是常用采样配置，`payload` 可携带业务补充配置。
        返回值：`PublishResult`。
        异常情况：事件名、payload 或 selection 非法时由协议层抛出异常。
        """

        event_payload = dict(payload or {})
        event_payload["stream_type"] = stream_type
        event_payload["mode"] = mode
        if rate_hz is not None:
            event_payload["rate_hz"] = rate_hz
        if duration_seconds is not None:
            event_payload["duration_seconds"] = duration_seconds
        return self.publish_event(
            "stream.control.configure.requested",
            stream_type=stream_type,
            payload=event_payload,
            selection=selection,
            timeout_seconds=timeout_seconds,
        )

    def query_assets(self, stream_type: str, freshness_seconds: float | None = None) -> list[AssetRef]:
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
        stream_type: str,
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

        主要逻辑：作为旧 SDK `submit_notification()` 的迁移便捷入口，底层进入
        Output Service，由 TTS、播放仲裁和输出 stream 统一处理。
        参数：`text` 为通知文本，`priority` 为优先级，`ttl_seconds` 为通知有效期。
        返回值：无。
        异常情况：同 `submit_text()`。
        """

        self.submit_text(text=text, priority=priority, ttl_seconds=ttl_seconds)

    def close_continuous_dialog(self, *, mode: str = "after_reply", reason: str = "model_requested") -> dict:
        """请求关闭连续对话窗口。

        主要逻辑：兼容老 SDK `close_continuous_dialog` 工具语义，但执行仍委托
        AudioChatApp 的音频会话生命周期；默认等待当前回复播放结束后关闭。
        参数：`mode` 当前支持 after_reply / close_after_reply / close_now。
        返回值：关闭调度结果。
        异常情况：非法 mode 时抛出 `ToolError`。
        """

        close_mode = "close_after_reply" if mode in {"after_reply", "close_after_reply"} else mode
        if close_mode not in {"close_after_reply", "close_now"}:
            raise ToolError("unsupported close mode", code=ErrorCode.INVALID_ARGUMENT, details={"mode": mode})
        session_id = self._app.active_session_id(self.user_id)
        self._app.close_audio_session(self.user_id, reason=reason, mode=close_mode)
        return {"scheduled": True, "mode": mode, "close_mode": close_mode, "session_id": session_id}

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
        stream_type: str,
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
            if isinstance(since, (int, float)) and ref.created_at <= float(since):
                continue
            if isinstance(since, str) and ref.asset_id <= since:
                continue
            yield ref


class RequestAssetInput(BaseModel):
    """请求传感器资产输入。"""

    stream_type: str = Field(description="要请求的 sensor.* stream 类型，例如 sensor.rgb。")
    freshness_seconds: float = Field(default=0, ge=0, description="允许复用缓存资产的最大秒数；0 表示必须请求新资产。")
    configure_payload: dict = Field(default_factory=dict, description="发给端侧的 stream 配置参数，不允许包含媒体字节。")
    timeout_seconds: float | None = Field(default=None, gt=0, description="等待端侧上传资产的超时时间，单位秒。")


class RequestAssetOutput(BaseModel):
    """请求传感器资产输出。"""

    asset_id: str | None = Field(default=None, description="资产 ID；超时或不可用时为空。")
    stream_type: str | None = Field(default=None, description="资产来源 stream 类型。")
    path: str | None = Field(default=None, description="本地调试路径。")
    mime_type: str | None = Field(default=None, description="资产 MIME 类型。")


class RequestAssetTool(BaseTool):
    """请求对话资产的协议原生 Tool 门面。

    主要功能：让 Agent 工具调用通过 `UserDeviceContext.request_asset()` 获取资产引用。
    主要方法：`run()`。
    """

    spec = ToolSpec(
        name="request_asset",
        description="请求端侧上传指定类型的传感器资产。需要抓拍照片时优先使用 capture_photo。",
        input_model=RequestAssetInput,
        output_model=RequestAssetOutput,
        capability_type="tool",
        tags=["asset", "stream", "sensor"],
    )

    def run(
        self,
        context: ToolContext | UserDeviceContext,
        input_data: dict | None = None,
        **kwargs,
    ):
        """执行资产请求工具。

        主要逻辑：直接转调协议原生 Context API。
        参数：`context` 为用户设备上下文，`stream_type` 为资产 stream，
        `freshness_seconds` 为缓存新鲜度，`configure_payload` 为端侧配置。
        返回值：`AssetRef` 或 `None`。
        异常情况：同 Context 方法。
        """
        if isinstance(context, UserDeviceContext):
            return context.request_asset(
                stream_type=str(kwargs.get("stream_type") or ""),
                freshness_seconds=float(kwargs.get("freshness_seconds") or 0),
                configure_payload=kwargs.get("configure_payload"),
                timeout_seconds=kwargs.get("timeout_seconds"),
            )
        return self._run_tool_context(context, dict(input_data or {}))

    async def _run_tool_context(self, context: ToolContext, input_data: dict) -> ToolResult:
        """按 ToolGateway 调用形态执行资产请求。"""

        asset = context.devices.request_asset(
            stream_type=str(input_data.get("stream_type") or ""),
            freshness_seconds=float(input_data.get("freshness_seconds") or 0),
            configure_payload=input_data.get("configure_payload"),
            timeout_seconds=input_data.get("timeout_seconds"),
        )
        return ToolResult.success(
            data=asset.__dict__ if asset is not None else None,
            assets=[asset] if asset is not None else [],
            message="asset requested" if asset is not None else "asset unavailable",
        )


class ConfigureAssetStreamTool(BaseTool):
    """配置端侧传感器 stream 的内置 Tool。"""

    class Input(BaseModel):
        stream_type: str = Field(description="要配置的 sensor.* stream 类型，例如 sensor.rgb 或 sensor.imu。")
        mode: str = Field(default="single", description="stream 模式，例如 single、continuous 或 stop。")
        payload: dict = Field(default_factory=dict, description="端侧配置参数，不允许包含媒体字节。")
        selection: Literal["first_available", "all"] = Field(default="first_available", description="匹配多台设备时的选择策略。")

    class Output(BaseModel):
        matched_count: int = Field(description="订阅匹配并经过选择策略后的设备数量。")
        delivered_count: int = Field(description="实际投递成功的设备数量。")

    spec = ToolSpec(
        name="configure_asset_stream",
        description="通过控制事件请求端侧配置 sensor.* stream，适合启动或停止连续传感器上传。",
        input_model=Input,
        output_model=Output,
        capability_type="tool",
        tags=["stream", "sensor"],
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        stream_type = str(input_data.get("stream_type") or "")
        payload = dict(input_data.get("payload") or {})
        payload.setdefault("mode", str(input_data.get("mode") or "single"))
        result = context.devices.publish_event(
            "stream.control.configure.requested",
            stream_type=stream_type,
            payload=payload,
            selection=str(input_data.get("selection") or "first_available"),
        )
        return ToolResult.success(data=result.__dict__, message="asset stream configure event published")


class CapturePhotoInput(BaseModel):
    """抓拍图片输入。"""

    reason: str = Field(default="agent_requested", description="抓拍原因，会写入端侧 stream 配置事件。")
    timeout_seconds: float | None = Field(default=10, description="等待端侧上传图片的超时时间。")
    freshness_seconds: float = Field(default=0, description="可复用缓存图片的最大秒数；需要新图时填 0。")


class CapturePhotoOutput(BaseModel):
    """抓拍图片输出。"""

    asset_id: str
    stream_type: str
    storage_uri: str
    path: str
    mime_type: str
    bytes: int | None = None
    metadata: dict = Field(default_factory=dict)


class CapturePhotoTool(BaseTool):
    """旧 SDK `capture_photo` 的内置兼容 Tool。

    主要功能：让模型继续用老工具名请求当前视觉资产；实现上只请求 `sensor.rgb`
    stream，不引入相机 RPC 或点对点 device_id。
    """

    spec = ToolSpec(
        name="capture_photo",
        description=(
            "当用户询问眼前画面、物体、文字、障碍物、路况等需要新的视觉信息才能回答的问题时调用。"
            "在 Realtime/Omni Agent Core 中，本工具会获取端侧最新照片并追加到当前多模态 conversation，"
            "让模型基于这张新照片继续回答；"
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

        主要逻辑：通过 `UserDeviceContext.capture_photo()` 发布 stream 配置事件并等待
        端侧上传图片；返回资产引用给 Agent Core。
        参数：`input_data` 可包含 reason、timeout_seconds、freshness_seconds。
        返回值：成功时包含 AssetRef，超时时返回结构化失败。
        异常情况：无匹配设备或等待超时会转为 `TIMEOUT` 失败。
        """

        asset = context.devices.capture_photo(
            reason=str(input_data.get("reason") or "agent_requested"),
            timeout_seconds=input_data.get("timeout_seconds"),
            freshness_seconds=float(input_data.get("freshness_seconds") or 0),
        )
        if asset is None:
            return ToolResult.failed(ToolError("photo capture timed out", code=ErrorCode.TIMEOUT))
        return ToolResult.success(
            data={
                "asset_id": asset.asset_id,
                "stream_type": asset.stream_type,
                "storage_uri": asset.path,
                "path": asset.path,
                "mime_type": asset.mime_type,
                "bytes": asset.metadata.get("payload_size"),
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
        selection: Literal["first_available", "all"] = Field(default="first_available", description="匹配多台设备时的选择策略。")

    class Output(BaseModel):
        matched_count: int = Field(description="订阅匹配并经过选择策略后的设备数量。")
        delivered_count: int = Field(description="实际投递成功的设备数量。")

    spec = ToolSpec(
        name="publish_device_command",
        description="按订阅发布 control.device.command.requested 控制事件，不接受 device_id。",
        input_model=Input,
        output_model=Output,
        capability_type="tool",
        tags=["device", "control"],
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        payload = {
            "command_name": str(input_data.get("command_name") or ""),
            "params": dict(input_data.get("params") or {}),
        }
        result = context.devices.publish_event(
            "control.device.command.requested",
            payload=payload,
            selection=str(input_data.get("selection") or "first_available"),
        )
        return ToolResult.success(data=result.__dict__, message="device command event published")


class StartPhoneVideoLinkInput(BaseModel):
    """启动视频画面连接输入。"""

    link_mode: str = Field(default="direct", description="兼容字段；新版 SDK 仅记录到 payload。")
    frame_interval_ms: int = Field(default=500, ge=1, description="期望上传间隔，单位毫秒。")
    duration_seconds: float | None = Field(default=None, description="可选持续时间，由端侧自行停止。")


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
    """旧 SDK `start_phone_video_link` 的协议原生兼容 Tool。

    主要功能：请求可生产 `sensor.rgb` 的端侧建立连续 RGB stream。新版 SDK 不再暴露
    手机/眼镜点对点连接细节，因此本工具只返回配置事件投递结果和 link_id。
    """

    spec = ToolSpec(
        name="start_phone_video_link",
        description=(
            "当任务需要手机或其他端侧摄像头持续回传画面时调用，例如持续观察、找物、导航或红绿灯辅助。"
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

        主要逻辑：发布 `stream.control.configure.requested`，由设备订阅策略决定具体
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
        result = context.devices.configure_stream(
            "sensor.rgb",
            mode="continuous",
            rate_hz=float(payload["rate_hz"]),
            duration_seconds=payload.get("duration_seconds"),
            payload=payload,
            selection="first_available",
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
            message="已请求端侧建立视频画面连接。" if result.delivered_count else "没有找到可建立视频画面连接的端侧。",
        )


class CloseContinuousDialogInput(BaseModel):
    """关闭连续对话输入。"""

    mode: Literal["after_reply", "close_after_reply", "close_now"] = Field(
        default="after_reply",
        description="关闭方式。after_reply 表示等当前回复播报完成后关闭连续对话。",
    )


class CloseContinuousDialogOutput(BaseModel):
    """关闭连续对话输出。"""

    scheduled: bool
    mode: str
    close_mode: str
    session_id: str


class CloseContinuousDialogTool(BaseTool):
    """旧 SDK `close_continuous_dialog` 的内置兼容 Tool。"""

    spec = ToolSpec(
        name="close_continuous_dialog",
        description=(
            "只能在用户明确表达结束连续对话、希望助手安静、先不用继续听、先这样、等会儿再说等意图时调用。"
            "不要因为一次普通问题已经回答完成就调用。"
        ),
        input_model=CloseContinuousDialogInput,
        output_model=CloseContinuousDialogOutput,
        capability_type="tool",
        tags=["voice", "dialog", "system"],
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """安排关闭连续对话。"""

        result = context.devices.close_continuous_dialog(
            mode=str(input_data.get("mode") or "after_reply"),
            reason="model_tool_requested",
        )
        return ToolResult.success(data=result, message="已安排关闭连续对话。")


class QueryDeviceStateTool(BaseTool):
    """查询当前用户 active device set 的内置 Tool。"""

    class Input(BaseModel):
        include_subscriptions: bool = Field(default=True, description="是否返回设备订阅摘要。")

    class Output(BaseModel):
        devices: list[dict] = Field(description="当前用户在线设备快照列表。")
        count: int = Field(description="匹配设备数量。")

    spec = ToolSpec(
        name="query_device_state",
        description="查询当前用户在线设备、名称、properties 和订阅摘要。用户询问有哪些设备在线或设备状态时调用。",
        input_model=Input,
        output_model=Output,
        capability_type="tool",
        tags=["device", "debug", "system"],
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        devices = context.devices.get_devices()
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
        task_id: str = Field(description="要查询的 Task ID。")

    spec = ToolSpec(
        name="query_task_status",
        description="查询一个 server 侧任务的状态。",
        input_model=Input,
        capability_type="tool",
        tags=["task"],
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.tasks is None:
            return ToolResult.failed(ToolError("task engine is not configured", code=ErrorCode.PROTOCOL_ERROR))
        ref = context.tasks.query(str(input_data.get("task_id") or ""))
        return ToolResult.success(data=ref.__dict__, tasks=[ref], message=ref.state)


class CancelTaskTool(BaseTool):
    """取消 TaskEngine 任务的内置 Tool。"""

    class Input(BaseModel):
        task_id: str = Field(description="要取消的 Task ID。")
        reason: str = Field(default="tool_requested", description="取消原因。")

    spec = ToolSpec(
        name="cancel_task",
        description="取消一个 server 侧任务。只能在用户明确要求停止某个任务时调用。",
        input_model=Input,
        capability_type="tool",
        tags=["task"],
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.tasks is None:
            return ToolResult.failed(ToolError("task engine is not configured", code=ErrorCode.PROTOCOL_ERROR))
        ref = await context.tasks.cancel(str(input_data.get("task_id") or ""), reason=str(input_data.get("reason") or "tool_requested"))
        return ToolResult.success(data=ref.__dict__, tasks=[ref], message=ref.state)


class MemorySearchTool(BaseTool):
    """搜索长期记忆的内置 Tool。"""

    class Input(BaseModel):
        query: str = Field(description="要搜索的记忆关键词或问题。")
        limit: int = Field(default=5, ge=1, le=20, description="最多返回的记忆条数。")

    spec = ToolSpec(
        name="memory_search",
        description="搜索当前用户的长期记忆。",
        input_model=Input,
        capability_type="tool",
        tags=["memory"],
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.memory is None:
            return ToolResult.failed(ToolError("memory service is not configured", code=ErrorCode.PROTOCOL_ERROR))
        records = context.memory.search(
            user_id=context.user_id,
            query=str(input_data.get("query") or ""),
            limit=int(input_data.get("limit") or 5),
        )
        return ToolResult.success(
            data=[asdict(record) for record in records],
            message=f"{len(records)} memory records matched",
        )


class ManageMemoryTool(BaseTool):
    """写入长期记忆的内置 Tool。"""

    class Input(BaseModel):
        content: str = Field(description="要写入长期记忆的内容。")
        metadata: dict = Field(default_factory=dict, description="可选结构化元数据。")

    spec = ToolSpec(
        name="manage_memory",
        description="写入当前用户长期记忆。只有用户明确提供稳定偏好、身份信息或长期事实时调用。",
        input_model=Input,
        capability_type="tool",
        tags=["memory"],
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.memory is None:
            return ToolResult.failed(ToolError("memory service is not configured", code=ErrorCode.PROTOCOL_ERROR))
        record = context.memory.write(
            user_id=context.user_id,
            content=str(input_data.get("content") or ""),
            metadata=dict(input_data.get("metadata") or {}),
        )
        return ToolResult.success(data=asdict(record), message="memory written")


class ReadSkillTool(BaseTool):
    """读取受控 Skill 的内置 Tool。"""

    class Input(BaseModel):
        name: str = Field(description="要读取的 Skill 名称。")

    spec = ToolSpec(
        name="read_skill",
        description="读取配置 Skill roots 下的受控 Skill 文档。",
        input_model=Input,
        capability_type="tool",
        tags=["skill"],
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.skills is None:
            return ToolResult.failed(ToolError("skill service is not configured", code=ErrorCode.PROTOCOL_ERROR))
        document = context.skills.read_skill(str(input_data.get("name") or ""))
        return ToolResult.success(data=asdict(document), message=document.description)


class McpCallTool(BaseTool):
    """通过 MCP Gateway 调用 MCP tool 的内置 Tool。"""

    class Input(BaseModel):
        tool_name: str = Field(description="MCP tool 名称，例如 web.search 或 amap.route_plan。")
        arguments: dict = Field(default_factory=dict, description="传给 MCP tool 的结构化参数。")
        timeout_seconds: float | None = Field(default=None, gt=0, description="调用超时时间，单位秒。")

    spec = ToolSpec(
        name="mcp_call",
        description="调用配置中的 MCP tool。业务推荐封装成更具体的 Tool 后再暴露给模型。",
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
    ConfigureAssetStreamTool,
    CapturePhotoTool,
    PublishDeviceCommandTool,
    StartPhoneVideoLinkTool,
    CloseContinuousDialogTool,
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
