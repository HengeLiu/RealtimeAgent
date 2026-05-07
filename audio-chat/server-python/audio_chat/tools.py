from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator

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
    timeout_seconds: float | None = None

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行 Tool。

        主要逻辑：基类只声明接口，具体业务 Tool 覆盖实现。
        参数：`context` 为 SDK 注入上下文，`input_data` 为模型参数。
        返回值：`ToolResult`。
        异常情况：未覆盖时抛出 `ToolError`。
        """
        raise ToolError(f"tool {self.__class__.__name__} does not implement run()", code=ErrorCode.PROTOCOL_ERROR)


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
        name = tool.name or tool.__class__.__name__
        if not name:
            raise ToolError("tool name is required", code=ErrorCode.INVALID_ARGUMENT)
        if name in self._tools:
            raise ToolError(f"duplicate tool name: {name}", code=ErrorCode.PROTOCOL_ERROR)
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
                    tool_name = tool.name or tool.__class__.__name__
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

        return {
            "name": tool.name or tool.__class__.__name__,
            "description": tool.description,
            "parameters": self._input_schema(tool.input_model),
            "progress_message": getattr(tool, "progress_message", None),
            "timeout_seconds": getattr(tool, "timeout_seconds", None),
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
                return dict(schema_builder())
            except Exception:
                return {"type": "object", "properties": {}, "additionalProperties": True}
        if input_model is dict or input_model is None:
            return {"type": "object", "properties": {}, "additionalProperties": True}
        return {"type": "object", "properties": {}, "additionalProperties": True}


class ToolContextFactory:
    """ToolContext 工厂。

    主要功能：集中创建 Tool 执行上下文，确保设备能力只通过 UserDeviceContext 注入。
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
            coroutine = tool.run(context, input_data)
            timeout_seconds = getattr(tool, "timeout_seconds", None)
            if timeout_seconds:
                return await asyncio.wait_for(coroutine, timeout=timeout_seconds)
            return await coroutine
        except TimeoutError:
            return ToolResult.failed(ToolError("tool execution timeout", code=ErrorCode.TIMEOUT))
        except AudioChatError as exc:
            return ToolResult.failed(exc)
        except Exception as exc:
            return ToolResult.failed(ToolError(str(exc), code=ErrorCode.UNKNOWN))


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
            if self.policy.allowed(tool.name or tool.__class__.__name__)
            and self._skill_allowed(tool.name or tool.__class__.__name__)
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
        self._emit_progress_once(tool=tool, session_id=session_id)
        result = await self.executor.execute(
            tool,
            self.context_factory.create(user_id=user_id, session_id=session_id),
            input_data,
        )
        self._record_trace(trace_id, name, user_id, session_id, input_data, result, started)
        return result

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

    def _emit_progress_once(self, *, tool: BaseTool, session_id: str) -> None:
        progress_message = str(getattr(tool, "progress_message", "") or "").strip()
        tool_name = tool.name or tool.__class__.__name__
        key = (session_id, tool_name)
        if not progress_message or key in self._progress_emitted:
            return
        self._progress_emitted.add(key)
        if self.recorder and hasattr(self.recorder, "record_agent_event"):
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "tool.progress_message.emitted",
                    "tool_name": tool_name,
                    "message": progress_message,
                },
            )

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


@dataclass(frozen=True)
class DeviceSnapshot:
    """端侧设备的只读快照。

    主要功能：向 Tool / Task 暴露当前用户 active device set 的能力摘要。
    主要属性：`device_id` 只用于 debug 和只读展示，协议原生 Tool / Task API 不接受
    业务代码传入 device_id 做点对点投递。
    """

    device_id: str
    capabilities: dict


class DeviceHandle:
    """设备只读句柄。

    主要功能：保留兼容性的只读设备快照容器，不能承载命令、stream 配置或端侧任务 RPC。
    主要属性：`snapshot` 是 `DeviceSnapshot`。
    """

    def __init__(self, snapshot: DeviceSnapshot) -> None:
        self.snapshot = snapshot


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

    主要功能：Tool / Task 只能通过事件、资产、输出意图和 stream 写入器表达业务意图；
    不面向某个 device_id 编程，也不暴露端侧 RPC。
    主要方法：`publish_event()`、`open_output_stream()`、`request_asset()`、
    `query_assets()`、`watch_assets()`、`submit_text()`、`submit_audio()`。
    """

    def __init__(self, *, user_id: str, app) -> None:
        self.user_id = user_id
        self._app = app

    def get_devices(self, capability: str | None = None) -> list[DeviceSnapshot]:
        """返回当前用户 active device set 的只读快照。

        主要逻辑：从 Control Service 读取在线设备，并按 capability 可选过滤。
        参数：`capability` 为空时返回全部设备，否则只返回声明该能力的设备。
        返回值：`DeviceSnapshot` 列表。
        异常情况：无在线设备时返回空列表。
        """
        devices = []
        for record in self._app.control_service.get_active_device_set(self.user_id).devices:
            if capability is None or self._has_capability(record.capabilities, capability):
                devices.append(DeviceSnapshot(device_id=record.device_id, capabilities=record.capabilities))
        return devices

    def find_device(self, capability: str) -> DeviceHandle | None:
        """按 capability 返回只读设备句柄。

        主要逻辑：仅用于能力检查和 debug，不提供命令方法；真实通讯必须走协议原生 API。
        参数：`capability` 为能力名。
        返回值：匹配时返回 `DeviceHandle`，否则返回 `None`。
        异常情况：无。
        """
        devices = self.get_devices(capability)
        return DeviceHandle(devices[0]) if devices else None

    def publish_event(
        self,
        event_name: str,
        payload: dict | None = None,
        stream_type: str | None = None,
        require_capability: str | None = None,
        selection: str = "all",
        timeout_seconds: float | None = None,
    ) -> PublishResult:
        """发布协议控制事件。

        主要逻辑：事件按 user_id、订阅、stream_type、require_capability 和 selection
        匹配端侧；业务代码不能指定 device_id。
        参数：`event_name` 为协议事件名，`payload` 为事件载荷，`stream_type` 为可选
        stream 过滤，`require_capability` 为可选能力条件，`selection` 为选择策略，
        `timeout_seconds` 预留给未来 ACK 等待，本阶段不阻塞。
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
            require_capability=require_capability,
            selection=selection,
        )

    def open_output_stream(
        self,
        stream_type: str,
        codec: str,
        metadata: dict | None = None,
        require_capability: str | None = None,
        selection: str = "all",
    ) -> OutputStreamWriter:
        """打开 output stream 并返回写入器。

        主要逻辑：创建 `actuator.*` stream，底层按订阅、capability 和 selection
        选出消费端，并把后续 chunk 固定投递到这批设备。
        参数：`stream_type` 为 output stream 类型，`codec` 为编码，`metadata` 为可选
        元信息，`require_capability` 和 `selection` 为设备匹配策略。
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
            require_capability=require_capability,
            selection=selection,
        )
        return OutputStreamWriter(context=self, stream_id=handle.stream_id, session_id=session_id, stream_type=stream_type, format=format)

    def request_asset(
        self,
        stream_type: str,
        freshness_seconds: float,
        configure_payload: dict | None = None,
        timeout_seconds: float | None = None,
    ) -> AssetRef | None:
        """请求单个传感器资产。

        主要逻辑：委托 Asset Service 先查 freshness 缓存，未命中时发布
        `stream.control.configure.requested`，等待端侧通过 `sensor.*` stream 上传。
        参数：`stream_type` 为资产 stream，`freshness_seconds` 为缓存新鲜度，
        `configure_payload` 为配置载荷，`timeout_seconds` 为等待超时。
        返回值：`AssetRef` 或 `None`。
        异常情况：底层事件发布或文件存储失败时向上抛出。
        """
        return self._app.asset_service.request_asset(
            user_id=self.user_id,
            stream_type=stream_type,
            freshness_seconds=freshness_seconds,
            configure_payload=configure_payload,
            timeout_seconds=timeout_seconds,
        )

    def query_assets(self, stream_type: str, freshness_seconds: float | None = None) -> list[AssetRef]:
        """查询缓存资产窗口。

        主要逻辑：读取 Asset Service 缓存窗口，并按 freshness_seconds 可选过滤。
        参数：`stream_type` 为资产 stream，`freshness_seconds` 为最大年龄。
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
    ) -> AsyncIterator[AssetRef]:
        """持续读取资产 stream 写入的资产引用。

        主要逻辑：返回 Asset Service 的 async iterator，按 stream_type 和 correlation_id
        过滤，适合 Task 消费连续 sensor.rgb 帧。
        参数：`stream_type` 为资产 stream，`correlation_id` 为可选任务关联 ID，
        `timeout_seconds` 为无新资产时的退出时间。
        返回值：异步迭代器。
        异常情况：无。
        """
        return self._app.asset_service.watch_assets(
            user_id=self.user_id,
            stream_type=stream_type,
            correlation_id=correlation_id,
            timeout_seconds=timeout_seconds,
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

    @staticmethod
    def _has_capability(capabilities: dict, capability: str) -> bool:
        if capabilities.get(capability):
            return True
        return capability in capabilities.get("streams.produce", []) or capability in capabilities.get("streams.consume", [])


class RequestAssetTool(BaseTool):
    """请求对话资产的协议原生 Tool 门面。

    主要功能：让 Agent 工具调用通过 `UserDeviceContext.request_asset()` 获取资产引用。
    主要方法：`run()`。
    """

    name = "request_asset"
    description = "请求端侧上传指定类型的传感器资产。"

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

    name = "configure_asset_stream"
    description = "通过控制事件请求端侧配置 sensor.* stream。"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        stream_type = str(input_data.get("stream_type") or "")
        payload = dict(input_data.get("payload") or {})
        result = context.devices.publish_event(
            "stream.control.configure.requested",
            stream_type=stream_type,
            payload=payload,
            require_capability=input_data.get("require_capability") or stream_type,
            selection=str(input_data.get("selection") or "first_available"),
        )
        return ToolResult.success(data=result.__dict__, message="asset stream configure event published")


class PublishDeviceCommandTool(BaseTool):
    """发布端侧命令事件的内置 Tool。"""

    name = "publish_device_command"
    description = "按订阅和能力发布 control.device.command.requested 事件。"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        payload = {
            "command_name": str(input_data.get("command_name") or ""),
            "params": dict(input_data.get("params") or {}),
        }
        result = context.devices.publish_event(
            "control.device.command.requested",
            payload=payload,
            require_capability=input_data.get("require_capability"),
            selection=str(input_data.get("selection") or "first_available"),
        )
        return ToolResult.success(data=result.__dict__, message="device command event published")


class QueryDeviceStateTool(BaseTool):
    """查询当前用户 active device set 的内置 Tool。"""

    name = "query_device_state"
    description = "查询当前用户在线设备和能力摘要。"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        capability = input_data.get("capability")
        devices = context.devices.get_devices(str(capability) if capability else None)
        return ToolResult.success(
            data=[device.__dict__ for device in devices],
            message=f"{len(devices)} active devices matched",
        )


class QueryTaskStatusTool(BaseTool):
    """查询 TaskEngine 任务状态的内置 Tool。"""

    name = "query_task_status"
    description = "查询一个 server 侧任务的状态。"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.tasks is None:
            return ToolResult.failed(ToolError("task engine is not configured", code=ErrorCode.PROTOCOL_ERROR))
        ref = context.tasks.query(str(input_data.get("task_id") or ""))
        return ToolResult.success(data=ref.__dict__, tasks=[ref], message=ref.state)


class CancelTaskTool(BaseTool):
    """取消 TaskEngine 任务的内置 Tool。"""

    name = "cancel_task"
    description = "取消一个 server 侧任务。"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.tasks is None:
            return ToolResult.failed(ToolError("task engine is not configured", code=ErrorCode.PROTOCOL_ERROR))
        ref = await context.tasks.cancel(str(input_data.get("task_id") or ""), reason=str(input_data.get("reason") or "tool_requested"))
        return ToolResult.success(data=ref.__dict__, tasks=[ref], message=ref.state)


class MemorySearchTool(BaseTool):
    """搜索长期记忆的内置 Tool。"""

    name = "memory_search"
    description = "搜索当前用户的长期记忆。"

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

    name = "manage_memory"
    description = "写入当前用户长期记忆。"

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

    name = "read_skill"
    description = "读取配置 Skill roots 下的受控 Skill 文档。"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.skills is None:
            return ToolResult.failed(ToolError("skill service is not configured", code=ErrorCode.PROTOCOL_ERROR))
        document = context.skills.read_skill(str(input_data.get("name") or ""))
        return ToolResult.success(data=asdict(document), message=document.description)


class McpCallTool(BaseTool):
    """通过 MCP Gateway 调用 MCP tool 的内置 Tool。"""

    name = "mcp_call"
    description = "调用配置中的 MCP tool。"

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
    PublishDeviceCommandTool,
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
