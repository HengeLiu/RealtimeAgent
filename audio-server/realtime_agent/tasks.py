from __future__ import annotations

import importlib
import inspect
import asyncio
import json
import pkgutil
import time
import threading
from concurrent.futures import Future
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from realtime_agent.asset import ArtifactRef
from realtime_agent.errors import RealtimeAgentError, ErrorCode
from realtime_agent.protocol import Event, SERVER_PRODUCER_ID, new_id
from realtime_agent.tools import ToolContext

TASK_EVENT_TYPES = ("start", "process", "status", "finish", "cancel", "error")
TASK_EVENT_NAMES = {f"task.event.{event_type}" for event_type in TASK_EVENT_TYPES}

TERMINAL_TASK_STATES = {"finished", "cancelled", "failed"}

TASK_STATES = ("started", "finished", "cancelled", "failed")

LEGACY_TASK_STATE_MAP = {
    "scheduled": "started",
    "running": "started",
    "waiting_external": "started",
    "completed": "finished",
    "timeout": "failed",
}

TASK_TRANSITIONS = {
    ("started", "finished"),
    ("started", "cancelled"),
    ("started", "failed"),
}


@dataclass(frozen=True)
class TaskSpec:
    """Task 运行规格。

    主要功能：把 Task 类型、启动输入、版本、超时、取消能力和用户级并发限制收敛成稳定描述。
    主要属性：`task_type/input_model/start_tool_name/version/timeout_seconds/cancel_supported/max_running_per_user`。
    """

    task_type: str
    input_model: Any = dict
    start_tool_name: str | None = None
    version: str = "v1"
    timeout_seconds: float | None = None
    cancel_supported: bool = True
    max_running_per_user: int | None = None
    start_result_timeout_seconds: float = 0.3


@dataclass(frozen=True)
class TaskAgentReply:
    """Task 启动后给 Agent 的回复建议。

    主要功能：把“任务启动成功或失败后，Agent 应如何简短回应用户”的建议从
    任意字符串收敛成结构化数据，避免模型只看到裸 `TaskRef` 后自由发挥。
    """

    message: str = ""
    instructions: str = ""
    allow_direct_notify: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转换为可写入 TaskRef.metadata 的字典。"""

        return {
            "message": self.message,
            "instructions": self.instructions,
            "allow_direct_notify": self.allow_direct_notify,
        }


@dataclass(frozen=True)
class TaskRunResult:
    """Task.run() 的启动阶段返回值。

    主要功能：表达 Task actor 是否完成启动初始化，以及希望 Agent 对用户说什么。
    该对象只描述“启动阶段”，不代表长任务最终完成结果。
    """

    ok: bool = True
    agent_reply: TaskAgentReply | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def started(
        cls,
        *,
        message: str = "",
        instructions: str = "",
        allow_direct_notify: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> "TaskRunResult":
        """创建启动成功结果。"""

        return cls(
            ok=True,
            agent_reply=TaskAgentReply(
                message=message,
                instructions=instructions,
                allow_direct_notify=allow_direct_notify,
            ),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def failed(
        cls,
        *,
        message: str,
        instructions: str = "",
        allow_direct_notify: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> "TaskRunResult":
        """创建启动失败结果。"""

        return cls(
            ok=False,
            agent_reply=TaskAgentReply(
                message=message,
                instructions=instructions,
                allow_direct_notify=allow_direct_notify,
            ),
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class TaskRef:
    """任务引用。

    主要功能：向 Tool、Agent 和 runs 产物描述一个长任务，而不暴露执行器内部对象。
    主要属性：`task_id` 为任务标识，`task_type` 为任务类型，`state` 为当前状态。
    """

    task_id: str
    task_type: str
    state: str
    summary: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TaskSignal:
    """任务信号。

    主要功能：承载任务状态变化、通知和 Agent 决策所需的结构化数据。
    它只表示 Task 内部对外发出的信号，不等同于系统级协议事件。
    主要属性：`priority`、`dedupe_key`、`ttl_seconds` 可被 NotificationCoordinator 使用。
    """

    task_id: str
    task_type: str
    signal_name: str
    user_id: str
    session_id: str | None = None
    payload: dict = field(default_factory=dict)
    priority: str = "normal"
    dedupe_key: str | None = None
    ttl_seconds: int = 0
    requires_agent_decision: bool = False
    allow_direct_notify: bool = True
    artifacts: list[ArtifactRef] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class TaskEventView:
    """Task Actor 收到的事件视图。

    主要功能：把系统事件 `task.event.*` 解析成 Task 开发者可读的结构。
    主要属性：`task_id` 定位具体任务实例，`task_event_type` 限制为 start/process/status/finish/cancel/error。
    """

    event: Event
    task_id: str
    task_type: str
    task_event_type: str


@dataclass(kw_only=True)
class _TaskContextBase(ToolContext):
    """Task 上下文私有基类。

    主要功能：继承 ToolContext 的短生命周期能力，并由 TaskEngine 额外注入任务引用、
    信号桥和 TaskEngine。Task 通过该上下文使用长时 stream、异步命令和任务状态流转；
    不直接操作消息、底层 WebSocket 或 speaker。
    """

    devices: Any
    task_ref: TaskRef
    bridge: "TaskSignalBridge | None" = None
    engine: "TaskEngine | None" = None
    metadata: dict = field(default_factory=dict)

    async def complete(self, payload: dict | None = None, *, summary: str = "") -> TaskRef:
        """把当前任务标记为完成。

        主要逻辑：委托 TaskEngine 完成状态流转并写入 `task.finished` 信号。
        参数：`payload` 为完成事件载荷，`summary` 为任务摘要。
        返回值：完成后的 `TaskRef`。
        异常情况：上下文未绑定 TaskEngine 时抛出协议错误。
        """

        if self.engine is None:
            raise RealtimeAgentError("task context has no engine", code=ErrorCode.PROTOCOL_ERROR)
        return self.engine.complete(self.task_ref.task_id, payload=payload or {}, summary=summary)

    async def fail(self, message: str, *, payload: dict | None = None) -> TaskRef:
        """把当前任务标记为失败。"""

        if self.engine is None:
            raise RealtimeAgentError("task context has no engine", code=ErrorCode.PROTOCOL_ERROR)
        return self.engine.fail(self.task_ref.task_id, message=message, payload=payload or {})

    async def schedule_signal(
        self,
        signal_name: str,
        *,
        payload: dict | None = None,
        delay_seconds: float = 0,
        priority: str = "normal",
        requires_agent_decision: bool = False,
        allow_direct_notify: bool = False,
    ) -> TaskRef | None:
        """调度一个任务信号。

        功能：
        1. 给业务 Task 提供稳定的延时信号入口，避免业务代码自建线程或定时器。
        2. 延时到达后把信号重新送回 TaskEngine，使 `on_signal()` 能处理到点、超时前提醒等状态。

        主要逻辑：
        1. `delay_seconds` 大于 0 时先异步等待。
        2. 构造 `TaskSignal`，复用当前任务编号、任务类型、用户和会话。
        3. 优先通过 TaskEngine 回流；没有绑定 engine 时退化为只通过 bridge 记录。

        参数：
        1. `signal_name`：任务信号名，例如 `timer.due`。
        2. `payload`：信号载荷。
        3. `delay_seconds`：延时秒数；小于等于 0 表示立即回流。
        4. `priority`：信号优先级。
        5. `requires_agent_decision`：是否需要进入 Agent 上下文同步。
        6. `allow_direct_notify`：是否允许信号桥直接转成通知输出。

        返回值：
        1. 绑定 TaskEngine 时返回信号处理后的 `TaskRef`。
        2. 仅绑定 bridge 时返回 `None`。

        异常情况：
        1. 信号处理失败时由 TaskEngine 或 bridge 抛出结构化异常。
        """

        signal = TaskSignal(
            task_id=self.task_ref.task_id,
            task_type=self.task_ref.task_type,
            signal_name=signal_name,
            user_id=self.user_id,
            session_id=self.session_id,
            payload=dict(payload or {}),
            priority=priority,
            requires_agent_decision=requires_agent_decision,
            allow_direct_notify=allow_direct_notify,
        )
        if delay_seconds > 0 and self.engine is not None:
            self.engine.schedule_signal(
                task_id=self.task_ref.task_id,
                signal_name=signal_name,
                payload=dict(payload or {}),
                delay_seconds=delay_seconds,
                priority=priority,
                requires_agent_decision=requires_agent_decision,
                allow_direct_notify=allow_direct_notify,
            )
            return self.task_ref
        if delay_seconds > 0:
            def _fire_delayed_signal() -> None:
                if self.bridge is not None:
                    self.bridge.handle_signal(signal)

            timer = threading.Timer(delay_seconds, _fire_delayed_signal)
            timer.daemon = True
            timer.start()
            return self.task_ref
        if self.engine is not None:
            return await self.engine.handle_signal(signal)
        if self.bridge is not None:
            self.bridge.handle_signal(signal)
        return None


@dataclass(kw_only=True)
class TaskContext(_TaskContextBase):
    """Task 执行上下文。

    主要功能：作为业务 Task 的公开上下文类型，提供长时设备能力、输出、资产和任务状态流转方法。
    """

    devices: Any = None


class BaseTask:
    """业务 Task 基类。

    主要功能：定义长任务稳定扩展面，自动发现只注册继承该类的具体子类。
    业务开发者只覆盖 `run()` 和 `on_*()` 回调；`_process_*()` 是 Task Core
    的内部模板方法，用来注入 finish/error/cancel 的状态流转逻辑。
    """

    task_type: str = ""
    description: str = ""
    input_model: Any = dict
    start_tool_name: str | None = None
    version: str = "v1"
    timeout_seconds: float | None = None
    cancel_supported: bool = True
    max_running_per_user: int | None = None
    task_spec: TaskSpec | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """同步显式 TaskSpec 到旧类属性。

        主要逻辑：新 Task 推荐声明 `task_spec = TaskSpec(...)`；为了兼容发现器、
        测试和少量旧代码读取 `task_cls.task_type/input_model`，在类创建时回填这些
        稳定属性。
        """

        super().__init_subclass__(**kwargs)
        declared = getattr(cls, "task_spec", None)
        if not isinstance(declared, TaskSpec):
            return
        cls.task_type = declared.task_type
        cls.input_model = declared.input_model
        cls.start_tool_name = declared.start_tool_name
        cls.version = declared.version
        cls.timeout_seconds = declared.timeout_seconds
        cls.cancel_supported = declared.cancel_supported
        cls.max_running_per_user = declared.max_running_per_user
        cls.start_result_timeout_seconds = declared.start_result_timeout_seconds

    @classmethod
    def spec(cls) -> TaskSpec:
        """返回 Task 运行规格。

        主要逻辑：优先读取显式 `task_spec`，兼容旧类属性声明。
        参数：无。
        返回值：`TaskSpec`。
        异常情况：`task_type` 为空时由注册表负责报错。
        """

        declared = getattr(cls, "task_spec", None)
        if isinstance(declared, TaskSpec):
            return declared
        return TaskSpec(
            task_type=cls.task_type or cls.__name__,
            input_model=getattr(cls, "input_model", dict),
            start_tool_name=getattr(cls, "start_tool_name", None),
            version=str(getattr(cls, "version", "v1") or "v1"),
            timeout_seconds=getattr(cls, "timeout_seconds", None),
            cancel_supported=bool(getattr(cls, "cancel_supported", True)),
            max_running_per_user=getattr(cls, "max_running_per_user", None),
            start_result_timeout_seconds=float(getattr(cls, "start_result_timeout_seconds", 0.3) or 0),
        )

    async def run(self, context: TaskContext) -> TaskRunResult | None:
        """任务后台入口。

        主要逻辑：默认兼容旧任务，把启动行为委托给 `on_start()`；新任务可以覆盖
        本方法启动后台流程，然后返回启动阶段的 `TaskRunResult`。
        参数：`context` 为 SDK 注入上下文。
        返回值：启动阶段结果；旧任务可以返回 None。
        异常情况：异常会被 TaskRunner 捕获并转换为 `task.event.error`。
        """

        await self._invoke_event_hook("on_start", context, None)
        return None

    async def on_start(self, context: TaskContext, event: TaskEventView | None = None) -> None:
        """任务启动回调。

        主要逻辑：基类只声明接口，子类按需覆盖。
        参数：`context` 为 SDK 注入上下文，`event` 为可选任务事件。
        返回值：无。
        异常情况：无。
        """
        return None

    async def on_process(self, context: TaskContext, event: TaskEventView) -> None:
        """任务过程事件回调。"""
        return None

    async def on_status(self, context: TaskContext, event: TaskEventView) -> None:
        """任务状态事件回调。"""
        return None

    async def on_finish(self, context: TaskContext, event: TaskEventView) -> None:
        """任务完成事件回调。"""
        return None

    async def on_cancel(self, context: TaskContext, event: TaskEventView | None = None) -> None:
        """任务取消事件回调。"""
        return None

    async def on_error(self, context: TaskContext, event: TaskEventView) -> None:
        """任务错误事件回调。"""
        return None

    async def on_signal(self, context: TaskContext, signal: TaskSignal) -> None:
        """兼容旧版 TaskSignal 回调。

        主要逻辑：保留旧扩展点，新的 Task Core 不再把 TaskSignal 当作 actor 输入。
        """
        return None

    async def _process_start(self, context: TaskContext, event: TaskEventView) -> None:
        await self._invoke_event_hook("on_start", context, event)

    async def _process_process(self, context: TaskContext, event: TaskEventView) -> None:
        await self._invoke_event_hook("on_process", context, event)

    async def _process_status(self, context: TaskContext, event: TaskEventView) -> None:
        await self._invoke_event_hook("on_status", context, event)

    async def _process_finish(self, context: TaskContext, event: TaskEventView) -> None:
        payload = dict(event.event.payload or {})
        try:
            await self._invoke_event_hook("on_finish", context, event)
        except Exception as exc:  # noqa: BLE001
            await context.fail(
                str(exc) or "任务完成事件处理失败",
                payload={"reason": "finish_handler_failed", "raw_error": str(exc), **payload},
            )
            return
        result = payload.get("result")
        if not isinstance(result, dict):
            result = {k: v for k, v in payload.items() if k not in {"task_id", "task_type", "summary", "cause"}}
        summary = str(payload.get("summary") or "")
        await context.complete(result, summary=summary)

    async def _process_cancel(self, context: TaskContext, event: TaskEventView) -> None:
        payload = dict(event.event.payload or {})
        try:
            await self._invoke_event_hook("on_cancel", context, event)
        finally:
            if context.engine is not None:
                context.engine._mark_cancelled(context.task_ref.task_id, reason=str(payload.get("reason") or "cancelled"))

    async def _process_error(self, context: TaskContext, event: TaskEventView) -> None:
        payload = dict(event.event.payload or {})
        message = str(payload.get("user_message") or payload.get("message") or "任务执行失败")
        try:
            await self._invoke_event_hook("on_error", context, event)
        except Exception as exc:  # noqa: BLE001
            payload = {"handler_error": str(exc), **payload}
        await context.fail(message, payload=payload)

    async def _invoke_event_hook(
        self,
        hook_name: str,
        context: TaskContext,
        event: TaskEventView | None,
    ) -> None:
        """调用开发者事件回调，并兼容旧版只接收 context 的方法签名。"""

        hook = getattr(self, hook_name)
        parameters = list(inspect.signature(hook).parameters.values())
        if len(parameters) <= 1 or event is None:
            result = hook(context)
        else:
            result = hook(context, event)
        if inspect.isawaitable(result):
            await result


class TaskStateMachine:
    """任务状态机。

    主要功能：约束任务状态只能沿设计文档允许路径流转。
    """

    def transition(self, current: str, target: str) -> str:
        current = _normalize_task_state(current)
        target = _normalize_task_state(target)
        if current == target:
            return target
        if (current, target) not in TASK_TRANSITIONS:
            raise RealtimeAgentError(f"invalid task transition: {current}->{target}", code=ErrorCode.PROTOCOL_ERROR)
        return target


class TaskStore:
    """进程内任务存储。

    主要功能：保存 TaskRef 和信号，可作为持久化 store 的内存基类。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRef] = {}
        self._signals: list[TaskSignal] = []

    def put(self, ref: TaskRef) -> None:
        self._tasks[ref.task_id] = ref

    def get(self, task_id: str) -> TaskRef:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise RealtimeAgentError(f"unknown task: {task_id}", code=ErrorCode.NOT_FOUND) from exc

    def append_signal(self, signal: TaskSignal) -> None:
        self._signals.append(signal)

    def signals_for_task(self, task_id: str) -> list[TaskSignal]:
        """返回某个任务的信号列表。"""

        return [signal for signal in self._signals if signal.task_id == task_id]

    def list_tasks(self) -> list[TaskRef]:
        """列出全部任务快照。"""

        return list(self._tasks.values())

    def list_unfinished(self) -> list[TaskRef]:
        """列出未进入终态的任务快照。"""

        return [ref for ref in self._tasks.values() if ref.state not in TERMINAL_TASK_STATES]


class JsonlTaskStore(TaskStore):
    """JSONL 持久化任务存储。

    主要功能：把 TaskRef 快照和 TaskSignal 追加写入 jsonl，重启后可重放恢复。
    主要属性：`root` 为存储目录，`tasks_path/signals_path` 为落地文件。
    """

    def __init__(self, root: str | Path) -> None:
        super().__init__()
        self.root = Path(root)
        self.tasks_path = self.root / "tasks.jsonl"
        self.signals_path = self.root / "task-signals.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self._load()

    def put(self, ref: TaskRef) -> None:
        super().put(ref)
        self._append_jsonl(self.tasks_path, {"record_type": "task.snapshot", "task": _task_ref_to_dict(ref)})

    def append_signal(self, signal: TaskSignal) -> None:
        super().append_signal(signal)
        self._append_jsonl(self.signals_path, {"record_type": "task.signal", "signal": _task_signal_to_dict(signal)})

    def _load(self) -> None:
        """重放 jsonl 文件，恢复任务和事件内存索引。"""

        if self.tasks_path.exists():
            for record in _read_jsonl(self.tasks_path):
                task_data = record.get("task") if isinstance(record, dict) else None
                if isinstance(task_data, dict):
                    ref = _task_ref_from_dict(task_data)
                    self._tasks[ref.task_id] = ref
        if self.signals_path.exists():
            for record in _read_jsonl(self.signals_path):
                signal_data = record.get("signal") if isinstance(record, dict) else None
                if isinstance(signal_data, dict):
                    self._signals.append(_task_signal_from_dict(signal_data))

    @staticmethod
    def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


class TaskRegistry:
    """Task 注册表。"""

    def __init__(self) -> None:
        self._tasks: dict[str, type[BaseTask]] = {}

    def register(self, task_cls: type[BaseTask]) -> None:
        task_type = task_cls.spec().task_type or task_cls.task_type or task_cls.__name__
        if not task_type:
            raise RealtimeAgentError("task_type is required", code=ErrorCode.INVALID_ARGUMENT)
        if task_type in self._tasks:
            raise RealtimeAgentError(f"duplicate task_type: {task_type}", code=ErrorCode.PROTOCOL_ERROR)
        self._tasks[task_type] = task_cls

    def get(self, task_type: str) -> type[BaseTask]:
        if task_type not in self._tasks:
            raise RealtimeAgentError(f"unknown task: {task_type}", code=ErrorCode.NOT_FOUND)
        return self._tasks[task_type]

    def spec(self, task_type: str) -> TaskSpec:
        """返回指定 Task 的运行规格。"""

        return self.get(task_type).spec()

    def list_task_types(self) -> list[str]:
        """列出已注册 Task 类型。"""

        return sorted(self._tasks)


class TaskAutoDiscovery:
    """Task 自动发现器。"""

    def __init__(self) -> None:
        self.errors: list[dict[str, str]] = []

    def discover(
        self,
        packages: list[str],
        *,
        recursive: bool = False,
        fail_fast: bool = True,
    ) -> list[type[BaseTask]]:
        """扫描 Task 包。

        主要逻辑：导入配置包，按需递归扫描子模块，发现非内部、非抽象的 Task 类。
        参数：`packages` 为模块路径列表，`recursive` 控制递归扫描，`fail_fast` 控制错误策略。
        返回值：Task 类列表。
        异常情况：导入失败和 `task_type` 重复时按 `fail_fast` 决定抛出或记录。
        """
        tasks: list[type[BaseTask]] = []
        seen: dict[str, str] = {}
        for package in packages:
            for module in self._iter_modules(package, recursive=recursive, fail_fast=fail_fast):
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if not self._is_concrete_task(name, obj):
                        continue
                    task_type = obj.spec().task_type or obj.task_type or obj.__name__
                    owner = f"{obj.__module__}.{obj.__name__}"
                    previous = seen.get(task_type)
                    if previous is not None:
                        error = {
                            "package": package,
                            "module": obj.__module__,
                            "error": f"duplicate task_type: {task_type}",
                            "previous": previous,
                            "current": owner,
                        }
                        if fail_fast:
                            raise RealtimeAgentError(error["error"], code=ErrorCode.PROTOCOL_ERROR, details=error)
                        self.errors.append(error)
                        continue
                    seen[task_type] = owner
                    tasks.append(obj)
        return tasks

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
            raise RealtimeAgentError("task discovery import failed", code=ErrorCode.PROTOCOL_ERROR, details=error) from exc
        self.errors.append(error)

    @staticmethod
    def _is_concrete_task(name: str, obj: type) -> bool:
        return (
            name
            and not name.startswith("_")
            and obj is not BaseTask
            and issubclass(obj, BaseTask)
            and not inspect.isabstract(obj)
        )


class TaskSignalBridge:
    """TaskSignal 桥接层。

    主要功能：Task 不直接写消息、不直接播放、不直接重入 Agent；信号统一通过本层
    记录，并在需要 Agent 决策时回灌为 Text 可见消息。
    """

    def __init__(self, *, recorder: Any = None, output_service: Any = None, control_service: Any = None) -> None:
        self.recorder = recorder
        self.output_service = output_service
        self.control_service = control_service

    def handle_signal(self, signal: TaskSignal) -> None:
        """处理任务信号。

        主要逻辑：记录 task signal；需要 Agent 决策的信号写入上下文同步记录；允许直
        发的信号才交给通知协调层。
        参数：`signal` 为任务信号。
        返回值：无。
        异常情况：底层记录或输出失败时向上抛出。
        """
        if self.recorder and hasattr(self.recorder, "record_task_signal"):
            self.recorder.record_task_signal(signal.session_id or signal.task_id, _task_signal_to_dict(signal))
        if signal.requires_agent_decision and self.recorder and hasattr(self.recorder, "record_agent_event"):
            self.recorder.record_agent_event(
                signal.session_id or signal.task_id,
                {
                    "event": "task.requires_agent_context_sync",
                    "task_id": signal.task_id,
                    "task_type": signal.task_type,
                    "task_signal_name": signal.signal_name,
                    "payload": signal.payload,
                },
            )
            self.recorder.record_agent_event(
                signal.session_id or signal.task_id,
                {
                    "event": "context.source.added",
                    "source_id": f"task_signal:{signal.task_id}:{signal.signal_name}",
                    "source_kind": "runtime",
                    "source_name": "task_signal",
                    "included": True,
                    "task_id": signal.task_id,
                    "task_type": signal.task_type,
                    "task_signal_name": signal.signal_name,
                },
            )
            if self.control_service is not None and signal.user_id and signal.session_id:
                self.control_service.append_message(
                    signal.user_id,
                    {
                        "session_id": signal.session_id,
                        "role": "user",
                        "content": _task_signal_message_content(signal),
                        "event": "task_signal.result",
                        "source": "task_signal_bridge",
                        "task_id": signal.task_id,
                        "task_type": signal.task_type,
                        "task_signal_name": signal.signal_name,
                    },
                )
        if signal.allow_direct_notify and self.output_service and hasattr(self.output_service, "notify_task_signal"):
            try:
                self.output_service.notify_task_signal(signal)
                if self.recorder and hasattr(self.recorder, "record_agent_event"):
                    self.recorder.record_agent_event(
                        signal.session_id or signal.task_id,
                        {
                            "event": "context.notification.recorded",
                            "source_id": f"task:{signal.task_id}:{signal.signal_name}",
                            "channel": "output",
                            "event_name": "task_signal",
                            "model_visible": bool(signal.requires_agent_decision),
                            "task_id": signal.task_id,
                            "task_type": signal.task_type,
                            "task_signal_name": signal.signal_name,
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                if self.recorder and hasattr(self.recorder, "record_system_event"):
                    self.recorder.record_system_event(
                        {
                            "event": "system.error.raised",
                            "component": "TaskSignalBridge",
                            "message": str(exc),
                            "task_id": signal.task_id,
                            "task_type": signal.task_type,
                            "task_signal_name": signal.signal_name,
                        }
                    )

    def convert_signal_to_agent_turn(self, signal: TaskSignal) -> dict:
        """转换任务信号为 Agent 可读轮次。

        主要逻辑：保留 task_id、task_type、signal_name 和 payload。
        参数：`signal` 为任务信号。
        返回值：字典。
        异常情况：无。
        """
        return {
            "role": "tool",
            "task_id": signal.task_id,
            "task_type": signal.task_type,
            "signal_name": signal.signal_name,
            "payload": signal.payload,
        }


class TaskExecutor:
    """兼容旧版测试和扩展的 Task 执行器。"""

    async def start(self, task: BaseTask, context: TaskContext) -> None:
        await task.run(context)

    async def signal(self, task: BaseTask, context: TaskContext, signal: TaskSignal) -> None:
        await task.on_signal(context, signal)

    async def cancel(self, task: BaseTask, context: TaskContext) -> None:
        await task.on_cancel(context)


class TaskRunner:
    """Task 后台运行器。

    主要功能：在独立事件循环中运行 Task actor，保证 `TaskEngine.create()` 能快速返回
    TaskRef，并让后续 `task.event.*` 事件异步投递到对应 Task 实例。
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._futures: dict[str, set[Future]] = {}

    def submit(self, task_id: str, coro: Any) -> Future:
        """提交一个 Task 协程到后台事件循环。"""

        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        with self._lock:
            self._futures.setdefault(task_id, set()).add(future)

        def _cleanup(done: Future) -> None:
            with self._lock:
                futures = self._futures.get(task_id)
                if futures is not None:
                    futures.discard(done)
                    if not futures:
                        self._futures.pop(task_id, None)

        future.add_done_callback(_cleanup)
        return future

    def cancel_task(self, task_id: str) -> None:
        """取消某个任务仍在后台运行的协程。"""

        with self._lock:
            futures = list(self._futures.get(task_id, set()))
        for future in futures:
            future.cancel()

    def shutdown(self) -> None:
        """关闭后台事件循环。"""

        with self._lock:
            futures = [future for group in self._futures.values() for future in group]
            self._futures.clear()
            loop = self._loop
        for future in futures:
            future.cancel()
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            ready = threading.Event()

            def _run_loop() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                with self._lock:
                    self._loop = loop
                ready.set()
                loop.run_forever()
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()

            self._thread = threading.Thread(target=_run_loop, name="realtime-agent-task-runner", daemon=True)
            self._thread.start()
        ready.wait(timeout=2)
        if self._loop is None:
            raise RealtimeAgentError("task runner loop did not start", code=ErrorCode.PROTOCOL_ERROR)
        return self._loop


class TaskScheduler:
    """Task 调度器。

    主要功能：集中处理恢复、超时判定和终态判断。当前实现采用查询时惰性扫
    描，避免测试和 CLI 中因后台事件循环生命周期不同产生残留任务。
    """

    def __init__(self, *, now: Any = None) -> None:
        self._now = now or time.time

    def deadline_for(self, *, started_at: float, timeout_seconds: float | None) -> float | None:
        """根据启动时间和超时配置计算 deadline。"""

        if timeout_seconds is None or timeout_seconds <= 0:
            return None
        return started_at + timeout_seconds

    def expired(self, ref: TaskRef) -> bool:
        """判断任务是否已超时。"""

        if ref.state in TERMINAL_TASK_STATES:
            return False
        deadline_at = ref.metadata.get("deadline_at")
        return isinstance(deadline_at, (int, float)) and deadline_at <= self._now()

    def recoverable(self, ref: TaskRef) -> bool:
        """判断任务是否需要恢复 Task 实例。"""

        return ref.state not in TERMINAL_TASK_STATES


class TaskEngine:
    """Task Engine 最小实现。

    主要功能：创建任务引用、执行任务启动回调，并通过 TaskSignalBridge 回流信号。
    """

    def __init__(
        self,
        *,
        registry: TaskRegistry | None = None,
        store: TaskStore | None = None,
        state_machine: TaskStateMachine | None = None,
        executor: TaskExecutor | None = None,
        runner: TaskRunner | None = None,
        bridge: TaskSignalBridge | None = None,
        scheduler: TaskScheduler | None = None,
        device_context_factory: Any = None,
        output_context_factory: Any = None,
        asset_context_factory: Any = None,
        max_running_per_user: int = 16,
    ) -> None:
        self.registry = registry or TaskRegistry()
        self.store = store or TaskStore()
        self.state_machine = state_machine or TaskStateMachine()
        self.executor = executor or TaskExecutor()
        self.runner = runner or TaskRunner()
        self.bridge = bridge or TaskSignalBridge()
        self.scheduler = scheduler or TaskScheduler()
        self.device_context_factory = device_context_factory
        self.output_context_factory = output_context_factory
        self.asset_context_factory = asset_context_factory
        self.max_running_per_user = max_running_per_user
        self._instances: dict[str, BaseTask] = {}
        self._scheduled_signals: dict[str, threading.Timer] = {}
        self._schedule_metadata: dict[str, dict[str, Any]] = {}
        self._schedule_lock = threading.Lock()

    def register(self, task_cls: type[BaseTask]) -> None:
        self.registry.register(task_cls)

    def list_task_types(self) -> list[dict[str, Any]]:
        """列出当前已注册 Task 类型和运行规格。"""

        rows: list[dict[str, Any]] = []
        for task_type in self.registry.list_task_types():
            task_cls = self.registry.get(task_type)
            spec = task_cls.spec()
            rows.append(
                {
                    "task_type": spec.task_type,
                    "description": str(getattr(task_cls, "description", "") or ""),
                    "start_tool_name": spec.start_tool_name or _default_task_start_tool_name(spec.task_type),
                    "version": spec.version,
                    "timeout_seconds": spec.timeout_seconds,
                    "cancel_supported": spec.cancel_supported,
                    "max_running_per_user": spec.max_running_per_user,
                }
            )
        return rows

    def list_tasks(self, *, user_id: str | None = None, include_terminal: bool = True) -> list[TaskRef]:
        """列出任务快照。

        参数：`user_id` 可限制为单个用户，`include_terminal` 控制是否包含终态任务。
        返回值：符合条件的 `TaskRef` 列表。
        """

        refs: list[TaskRef] = []
        for ref in self.store.list_tasks():
            ref = self._expire_if_needed(ref)
            if user_id is not None and ref.metadata.get("user_id") != user_id:
                continue
            if not include_terminal and ref.state in TERMINAL_TASK_STATES:
                continue
            refs.append(ref)
        return refs

    def query(self, task_id: str) -> TaskRef:
        """查询任务引用。

        主要逻辑：从 TaskStore 返回稳定 TaskRef，不暴露 Task 实例。
        参数：`task_id` 为任务 ID。
        返回值：TaskRef。
        异常情况：任务不存在时抛出 `RealtimeAgentError`。
        """

        ref = self.store.get(task_id)
        return self._expire_if_needed(ref)

    def restore_unfinished(self) -> list[TaskRef]:
        """恢复未完成任务快照。

        主要逻辑：从 store 读取未终态任务，为仍存在注册类型的任务补回实例，并
        对已过期任务立即流转到 timeout。
        参数：无。
        返回值：恢复后仍未进入终态的任务列表。
        异常情况：未知 Task 类型会保留快照但不会创建实例。
        """

        restored: list[TaskRef] = []
        for ref in self.store.list_unfinished():
            ref = self._expire_if_needed(ref)
            if ref.state in TERMINAL_TASK_STATES:
                continue
            try:
                self._instances.setdefault(ref.task_id, self.registry.get(ref.task_type)())
            except RealtimeAgentError:
                continue
            restored.append(ref)
        return restored

    async def create(
        self,
        *,
        task_type: str,
        user_id: str,
        session_id: str | None = None,
        input_data: dict | None = None,
        summary: str = "",
    ) -> TaskRef:
        """创建并启动任务。

        主要逻辑：创建 `started` 引用，把 Task actor 交给后台 TaskRunner 运行，
        然后立即返回 TaskRef。
        参数：`task_type/user_id/session_id/input_data` 描述任务请求。
        返回值：最新 TaskRef。
        异常情况：未知任务、非法状态流转或 Task 启动异常会抛出结构化异常。
        """

        task_cls = self.registry.get(task_type)
        spec = task_cls.spec()
        self._reject_if_concurrency_exceeded(user_id=user_id, spec=spec)
        task_id = new_id("task")
        now = time.time()
        timeout_seconds = _resolve_timeout_seconds(spec, input_data or {})
        deadline_at = self.scheduler.deadline_for(started_at=now, timeout_seconds=timeout_seconds)
        ref = TaskRef(
            task_id=task_id,
            task_type=task_type,
            state="started",
            summary=summary,
            metadata={
                "user_id": user_id,
                "session_id": session_id,
                "input": dict(input_data or {}),
                "version": spec.version,
                "timeout_seconds": timeout_seconds,
                "deadline_at": deadline_at,
                "cancel_supported": spec.cancel_supported,
                "max_running_per_user": spec.max_running_per_user or self.max_running_per_user,
                "created_at": now,
                "started_at": now,
                "updated_at": now,
            },
        )
        self.store.put(ref)
        task = task_cls()
        self._instances[task_id] = task
        self.emit_signal(
            TaskSignal(
                task_id=task_id,
                task_type=task_type,
                signal_name="task.started",
                user_id=user_id,
                session_id=session_id,
                payload={"state": ref.state, "input": dict(input_data or {})},
                allow_direct_notify=False,
            )
        )
        context = self._context(user_id=user_id, session_id=session_id, ref=ref)
        run_future = self.runner.submit(task_id, self._run_task(task_id, task, context))
        start_result_timeout = max(0.0, float(spec.start_result_timeout_seconds or 0))
        if start_result_timeout > 0:
            try:
                await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(run_future)), timeout=start_result_timeout)
            except asyncio.TimeoutError:
                pass
        await asyncio.sleep(0)
        return self.query(task_id)

    async def _run_task(self, task_id: str, task: BaseTask, context: TaskContext) -> TaskRunResult | None:
        """运行 Task actor，并把未捕获异常转换为 `task.event.error`。"""

        try:
            result = await task.run(context)
            if isinstance(result, TaskRunResult):
                self._apply_run_result(task_id, result)
            return result if isinstance(result, TaskRunResult) else None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            try:
                ref = self.query(task_id)
            except RealtimeAgentError:
                return
            if ref.state in TERMINAL_TASK_STATES:
                return
            event = Event(
                event_name="task.event.error",
                producer_id=SERVER_PRODUCER_ID,
                user_id=str(ref.metadata.get("user_id") or context.user_id),
                session_id=str(ref.metadata.get("session_id") or "") or context.session_id,
                payload={
                    "task_id": task_id,
                    "task_type": ref.task_type,
                    "reason": "runner_failed",
                    "raw_error": str(exc),
                    "message": "任务执行失败",
                },
            )
            self.dispatch_event(event)
            return None

    def _apply_run_result(self, task_id: str, result: TaskRunResult) -> TaskRef:
        """把 Task.run() 启动结果写回 TaskRef。

        主要逻辑：不直接改变任务终态，只把 Agent 回复建议和启动元数据写入
        `metadata.task_run_result`，供 TaskStartTool 返回给 Agent。
        """

        ref = self.query(task_id)
        metadata = {
            "task_run_result": {
                "ok": result.ok,
                "agent_reply": result.agent_reply.to_dict() if result.agent_reply is not None else {},
                **dict(result.metadata or {}),
            }
        }
        updated = replace(ref, metadata={**dict(ref.metadata), **metadata, "updated_at": time.time()})
        self.store.put(updated)
        return updated

    def dispatch_event(self, event: Event) -> TaskRef | None:
        """把 `task.event.*` 系统事件路由到具体 Task actor。

        主要逻辑：从 `payload.task_id` 定位任务实例，校验任务类型和状态，然后把
        事件提交给后台 TaskRunner。失败、跳过和接收都会写入 TaskSignal 作为运行产物。
        """

        try:
            view = self._parse_task_event(event)
        except RealtimeAgentError as exc:
            self._emit_dispatch_signal(
                event=event,
                signal_name="task.event.dispatch.failed",
                payload={"reason": exc.details.get("reason") if isinstance(exc.details, dict) else str(exc)},
            )
            return None
        try:
            ref = self.query(view.task_id)
        except RealtimeAgentError:
            self._emit_dispatch_signal(
                event=event,
                signal_name="task.event.dispatch.skipped",
                payload={"task_id": view.task_id, "reason": "task_not_found"},
            )
            return None
        if view.task_type and view.task_type != ref.task_type:
            self._emit_dispatch_signal(
                event=event,
                signal_name="task.event.dispatch.failed",
                ref=ref,
                payload={"reason": "task_type_mismatch", "actual_task_type": ref.task_type, "event_task_type": view.task_type},
            )
            return ref
        if ref.state in TERMINAL_TASK_STATES:
            self._emit_dispatch_signal(
                event=event,
                signal_name="task.event.dispatch.skipped",
                ref=ref,
                payload={"reason": "task_terminal", "state": ref.state},
            )
            return ref
        task = self._instances.get(ref.task_id)
        if task is None:
            if view.task_event_type == "finish":
                payload = dict(event.payload or {})
                self.complete(ref.task_id, payload=dict(payload.get("result") or payload), summary=str(payload.get("summary") or ""))
                self._emit_dispatch_signal(
                    event=event,
                    signal_name="task.event.dispatch.accepted",
                    ref=ref,
                    payload={"task_event_type": view.task_event_type, "fallback": "missing_instance_default_finish"},
                )
                return self.query(ref.task_id)
            if view.task_event_type == "error":
                payload = dict(event.payload or {})
                self.fail(
                    ref.task_id,
                    message=str(payload.get("user_message") or payload.get("message") or "任务执行失败"),
                    payload=payload,
                )
                self._emit_dispatch_signal(
                    event=event,
                    signal_name="task.event.dispatch.accepted",
                    ref=ref,
                    payload={"task_event_type": view.task_event_type, "fallback": "missing_instance_default_error"},
                )
                return self.query(ref.task_id)
            if view.task_event_type == "cancel":
                self._mark_cancelled(ref.task_id, reason=str((event.payload or {}).get("reason") or "cancelled"))
                self._emit_dispatch_signal(
                    event=event,
                    signal_name="task.event.dispatch.accepted",
                    ref=ref,
                    payload={"task_event_type": view.task_event_type, "fallback": "missing_instance_default_cancel"},
                )
                return self.query(ref.task_id)
            self._emit_dispatch_signal(
                event=event,
                signal_name="task.event.dispatch.skipped",
                ref=ref,
                payload={"reason": "task_instance_missing"},
            )
            return ref
        resolved_view = replace(view, task_type=ref.task_type)
        context = self._context(
            user_id=str(ref.metadata.get("user_id") or event.user_id or ""),
            session_id=str(ref.metadata.get("session_id") or "") or event.session_id,
            ref=ref,
        )
        self.runner.submit(ref.task_id, self._run_event(task, context, resolved_view))
        self._emit_dispatch_signal(
            event=event,
            signal_name="task.event.dispatch.accepted",
            ref=ref,
            payload={"task_event_type": resolved_view.task_event_type},
        )
        return ref

    async def _run_event(self, task: BaseTask, context: TaskContext, event: TaskEventView) -> None:
        """执行一个 Task actor 事件，并把处理异常转为 failed。"""

        method = getattr(task, f"_process_{event.task_event_type}")
        try:
            await method(context, event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            try:
                await context.fail(str(exc) or "任务事件处理失败", payload={"reason": "event_handler_failed", "raw_error": str(exc)})
            except Exception:
                raise

    async def handle_signal(self, signal: TaskSignal) -> TaskRef:
        """兼容旧版 TaskSignal 回流入口。

        主要逻辑：先把 TaskSignal 作为输出信号记录，再转换成 `task.event.*`
        投递给 Task actor。新的业务代码应直接使用 `dispatch_event()`。
        参数：`signal` 为任务信号。
        返回值：当前 TaskRef。
        异常情况：任务不存在时抛出 `RealtimeAgentError`。
        """

        ref = self.query(signal.task_id)
        if ref.state in TERMINAL_TASK_STATES:
            return ref
        self.emit_signal(signal)
        event = Event(
            event_name=_task_event_name_for_signal(signal.signal_name),
            producer_id=SERVER_PRODUCER_ID,
            user_id=signal.user_id,
            session_id=signal.session_id,
            payload={
                "task_id": signal.task_id,
                "task_type": signal.task_type,
                "signal_name": signal.signal_name,
                "cause": {"domain": "task_signal", "event": signal.signal_name},
                **dict(signal.payload or {}),
            },
        )
        self.dispatch_event(event)
        return self.query(signal.task_id)

    def schedule_signal(
        self,
        *,
        task_id: str,
        signal_name: str,
        payload: dict | None = None,
        delay_seconds: float = 0,
        user_id: str | None = None,
        session_id: str | None = None,
        priority: str = "normal",
        requires_agent_decision: bool = False,
        allow_direct_notify: bool = False,
    ) -> dict[str, Any]:
        """统一调度一次任务信号。

        主要逻辑：
        1. 校验任务存在和延迟参数。
        2. 由 TaskEngine 持有定时器，便于列出、取消和关闭。
        3. 到点后重新进入 `handle_signal()`，让业务 Task 的 `on_signal()` 处理。
        """

        ref = self.query(task_id)
        if ref.state in TERMINAL_TASK_STATES:
            raise RealtimeAgentError(f"cannot schedule signal for terminal task: {task_id}", code=ErrorCode.PROTOCOL_ERROR)
        normalized_signal_name = signal_name.strip()
        if not normalized_signal_name:
            raise RealtimeAgentError("signal_name is required", code=ErrorCode.INVALID_ARGUMENT)
        delay = float(delay_seconds)
        if delay < 0:
            raise RealtimeAgentError("delay_seconds must be >= 0", code=ErrorCode.INVALID_ARGUMENT)
        schedule_id = new_id("task_schedule")
        resolved_user_id = user_id if user_id is not None else str(ref.metadata.get("user_id") or "")
        resolved_session_id = session_id if session_id is not None else (str(ref.metadata.get("session_id") or "") or None)
        signal_payload = dict(payload or {})
        metadata = {
            "schedule_id": schedule_id,
            "task_id": task_id,
            "task_type": ref.task_type,
            "signal_name": normalized_signal_name,
            "delay_seconds": delay,
            "due_at": time.time() + delay,
            "user_id": resolved_user_id,
            "session_id": resolved_session_id,
        }

        def _fire() -> None:
            with self._schedule_lock:
                self._scheduled_signals.pop(schedule_id, None)
                self._schedule_metadata.pop(schedule_id, None)
            signal = TaskSignal(
                task_id=task_id,
                task_type=ref.task_type,
                signal_name=normalized_signal_name,
                user_id=resolved_user_id,
                session_id=resolved_session_id,
                payload=dict(signal_payload),
                priority=priority,
                requires_agent_decision=requires_agent_decision,
                allow_direct_notify=allow_direct_notify,
            )
            asyncio.run(self.handle_signal(signal))

        timer = threading.Timer(delay, _fire)
        timer.daemon = True
        with self._schedule_lock:
            self._scheduled_signals[schedule_id] = timer
            self._schedule_metadata[schedule_id] = metadata
        self.emit_signal(
            TaskSignal(
                task_id=task_id,
                task_type=ref.task_type,
                signal_name="task.signal.scheduled",
                user_id=resolved_user_id,
                session_id=resolved_session_id,
                payload=metadata,
                allow_direct_notify=False,
            )
        )
        timer.start()
        return dict(metadata)

    def cancel_scheduled_signal(self, schedule_id: str) -> bool:
        """取消尚未触发的调度信号。"""

        with self._schedule_lock:
            timer = self._scheduled_signals.pop(schedule_id, None)
            self._schedule_metadata.pop(schedule_id, None)
        if timer is None:
            return False
        timer.cancel()
        return True

    def list_scheduled_signals(self) -> list[dict[str, Any]]:
        """列出当前仍在等待触发的调度信号。"""

        with self._schedule_lock:
            rows = []
            for schedule_id, metadata in sorted(self._schedule_metadata.items()):
                timer = self._scheduled_signals.get(schedule_id)
                row = dict(metadata)
                row["alive"] = bool(timer and timer.is_alive())
                rows.append(row)
            return rows

    def shutdown(self) -> None:
        """关闭 TaskEngine 管理的后台调度器。"""

        with self._schedule_lock:
            timers = list(self._scheduled_signals.values())
            self._scheduled_signals.clear()
            self._schedule_metadata.clear()
        for timer in timers:
            timer.cancel()
        self.runner.shutdown()

    async def cancel(self, task_id: str, *, reason: str = "cancelled") -> TaskRef:
        """取消任务。

        主要逻辑：把取消请求转换为 `task.event.cancel`，由 Task actor 自己处理。
        参数：`task_id` 为任务 ID，`reason` 为取消原因。
        返回值：取消后的 TaskRef。
        异常情况：非法状态流转或任务不存在时抛出 `RealtimeAgentError`。
        """

        ref = self.query(task_id)
        if ref.state in TERMINAL_TASK_STATES:
            return ref
        if ref.metadata.get("cancel_supported") is False:
            raise RealtimeAgentError(f"task does not support cancel: {task_id}", code=ErrorCode.PROTOCOL_ERROR)
        event = Event(
            event_name="task.event.cancel",
            producer_id=SERVER_PRODUCER_ID,
            user_id=str(ref.metadata.get("user_id") or ""),
            session_id=str(ref.metadata.get("session_id") or "") or None,
            payload={"task_id": task_id, "task_type": ref.task_type, "reason": reason},
        )
        task = self._instances.get(task_id)
        if task is None:
            self.dispatch_event(event)
            return self.query(task_id)
        await self._run_event(
            task,
            self._context(
                user_id=str(ref.metadata.get("user_id") or ""),
                session_id=str(ref.metadata.get("session_id") or "") or None,
                ref=ref,
            ),
            TaskEventView(event=event, task_id=task_id, task_type=ref.task_type, task_event_type="cancel"),
        )
        return self.query(task_id)

    def complete(self, task_id: str, *, payload: dict | None = None, summary: str = "") -> TaskRef:
        """完成任务并写入 `task.finished` 信号。"""

        ref = self.query(task_id)
        if ref.state in TERMINAL_TASK_STATES:
            return ref
        ref = self._transition(ref, "finished", summary=summary or ref.summary)
        self.emit_signal(
            TaskSignal(
                task_id=ref.task_id,
                task_type=ref.task_type,
                signal_name="task.finished",
                user_id=str(ref.metadata.get("user_id") or ""),
                session_id=str(ref.metadata.get("session_id") or "") or None,
                payload={"state": ref.state, **dict(payload or {})},
                allow_direct_notify=False,
            )
        )
        return ref

    def fail(self, task_id: str, *, message: str, payload: dict | None = None) -> TaskRef:
        """失败任务并写入 `task.failed` 信号。"""

        ref = self.query(task_id)
        if ref.state in TERMINAL_TASK_STATES:
            return ref
        ref = self._transition(ref, "failed", metadata={"error": message})
        self.emit_signal(
            TaskSignal(
                task_id=ref.task_id,
                task_type=ref.task_type,
                signal_name="task.failed",
                user_id=str(ref.metadata.get("user_id") or ""),
                session_id=str(ref.metadata.get("session_id") or "") or None,
                payload={"message": message, **dict(payload or {})},
                priority="high",
                requires_agent_decision=True,
                allow_direct_notify=bool((payload or {}).get("allow_direct_notify", False)),
            )
        )
        return ref

    def _mark_cancelled(self, task_id: str, *, reason: str = "cancelled") -> TaskRef:
        """Task Core 内部取消收口。"""

        ref = self.query(task_id)
        if ref.state in TERMINAL_TASK_STATES:
            return ref
        self.runner.cancel_task(task_id)
        ref = self._transition(ref, "cancelled")
        self.emit_signal(
            TaskSignal(
                task_id=ref.task_id,
                task_type=ref.task_type,
                signal_name="task.cancelled",
                user_id=str(ref.metadata.get("user_id") or ""),
                session_id=str(ref.metadata.get("session_id") or "") or None,
                payload={"reason": reason},
                allow_direct_notify=False,
            )
        )
        return ref

    def emit_signal(self, signal: TaskSignal) -> None:
        """记录并桥接任务信号。"""

        self.store.append_signal(signal)
        self.bridge.handle_signal(signal)

    def _transition(self, ref: TaskRef, target: str, *, metadata: dict | None = None, summary: str | None = None) -> TaskRef:
        state = self.state_machine.transition(ref.state, target)
        merged_metadata = {**dict(ref.metadata), **dict(metadata or {}), "updated_at": time.time()}
        updated = replace(ref, state=state, metadata=merged_metadata, summary=ref.summary if summary is None else summary)
        self.store.put(updated)
        return updated

    def _context(self, *, user_id: str, session_id: str | None, ref: TaskRef) -> TaskContext:
        devices = self.device_context_factory(user_id=user_id) if callable(self.device_context_factory) else None
        output = self.output_context_factory(user_id=user_id) if callable(self.output_context_factory) else None
        assets = self.asset_context_factory(user_id=user_id) if callable(self.asset_context_factory) else None
        return TaskContext(
            user_id=user_id,
            session_id=session_id,
            task_ref=ref,
            devices=devices,
            output=output,
            assets=assets,
            bridge=self.bridge,
            engine=self,
            metadata=dict(ref.metadata),
        )

    def _expire_if_needed(self, ref: TaskRef) -> TaskRef:
        if not self.scheduler.expired(ref):
            return ref
        ref = self._transition(ref, "failed", metadata={"timeout_at": time.time(), "error": "timeout"})
        self.emit_signal(
            TaskSignal(
                task_id=ref.task_id,
                task_type=ref.task_type,
                signal_name="task.failed",
                user_id=str(ref.metadata.get("user_id") or ""),
                session_id=str(ref.metadata.get("session_id") or "") or None,
                payload={"reason": "timeout", "timeout_seconds": ref.metadata.get("timeout_seconds")},
                priority="high",
                requires_agent_decision=True,
                allow_direct_notify=False,
            )
        )
        return ref

    def _reject_if_concurrency_exceeded(self, *, user_id: str, spec: TaskSpec) -> None:
        limit = spec.max_running_per_user or self.max_running_per_user
        if limit <= 0:
            return
        running = [
            self._expire_if_needed(ref)
            for ref in self.store.list_tasks()
            if ref.metadata.get("user_id") == user_id and ref.state not in TERMINAL_TASK_STATES
        ]
        active_count = sum(1 for ref in running if ref.state not in TERMINAL_TASK_STATES)
        if active_count >= limit:
            raise RealtimeAgentError(
                "task running concurrency exceeded",
                code=ErrorCode.PROTOCOL_ERROR,
                details={"user_id": user_id, "limit": limit, "task_type": spec.task_type},
            )

    def _parse_task_event(self, event: Event) -> TaskEventView:
        event_name = str(event.event_name or "")
        if not event_name.startswith("task.event."):
            raise RealtimeAgentError(
                f"unsupported task event: {event_name}",
                code=ErrorCode.INVALID_ARGUMENT,
                details={"reason": "unsupported_event_name", "event_name": event_name},
            )
        task_event_type = event_name.removeprefix("task.event.")
        if task_event_type not in TASK_EVENT_TYPES:
            raise RealtimeAgentError(
                f"unsupported task event type: {task_event_type}",
                code=ErrorCode.INVALID_ARGUMENT,
                details={"reason": "unsupported_task_event_type", "task_event_type": task_event_type},
            )
        payload = dict(event.payload or {})
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            raise RealtimeAgentError(
                "task.event payload missing task_id",
                code=ErrorCode.INVALID_ARGUMENT,
                details={"reason": "missing_task_id"},
            )
        task_type = str(payload.get("task_type") or "").strip()
        return TaskEventView(event=event, task_id=task_id, task_type=task_type, task_event_type=task_event_type)

    def _emit_dispatch_signal(
        self,
        *,
        event: Event,
        signal_name: str,
        payload: dict | None = None,
        ref: TaskRef | None = None,
    ) -> None:
        event_payload = dict(event.payload or {})
        task_id = str(event_payload.get("task_id") or (ref.task_id if ref is not None else "task_unknown"))
        task_type = str(event_payload.get("task_type") or (ref.task_type if ref is not None else "unknown"))
        signal_payload = {
            "event_name": str(event.event_name),
            "event_id": event.event_id,
            **dict(payload or {}),
        }
        self.emit_signal(
            TaskSignal(
                task_id=task_id,
                task_type=task_type,
                signal_name=signal_name,
                user_id=str((ref.metadata if ref is not None else {}).get("user_id") or event.user_id or ""),
                session_id=(str((ref.metadata if ref is not None else {}).get("session_id") or "") or event.session_id),
                payload=signal_payload,
                allow_direct_notify=False,
            )
        )


def _resolve_timeout_seconds(spec: TaskSpec, input_data: dict) -> float | None:
    raw = input_data.get("timeout_seconds", spec.timeout_seconds)
    if raw is None:
        return None
    value = float(raw)
    return value if value > 0 else None


def _normalize_task_state(state: str) -> str:
    """把历史状态名映射到当前 Task Core 状态名。"""

    normalized = str(state or "started")
    return LEGACY_TASK_STATE_MAP.get(normalized, normalized)


def _task_event_name_for_signal(signal_name: str) -> str:
    """把兼容 TaskSignal 转换成 Task actor 事件名。"""

    normalized = str(signal_name or "").strip()
    if normalized.endswith(".done") or normalized.endswith(".due") or normalized.endswith(".completed"):
        return "task.event.finish"
    if normalized.endswith(".failed") or normalized.endswith(".error"):
        return "task.event.error"
    if normalized.endswith(".cancelled") or normalized.endswith(".cancel"):
        return "task.event.cancel"
    if normalized.endswith(".started"):
        return "task.event.status"
    return "task.event.process"


def _default_task_start_tool_name(task_type: str) -> str:
    """Return the model-visible start tool name for a task type."""

    normalized = str(task_type or "").strip()
    if normalized.endswith("_task"):
        return f"start_{normalized}"
    return f"start_{normalized}_task"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            records.append(json.loads(stripped))
    return records


def _task_ref_to_dict(ref: TaskRef) -> dict[str, Any]:
    return {
        "task_id": ref.task_id,
        "task_type": ref.task_type,
        "state": ref.state,
        "summary": ref.summary,
        "metadata": dict(ref.metadata),
    }


def _task_ref_from_dict(data: dict[str, Any]) -> TaskRef:
    state = _normalize_task_state(str(data.get("state") or "started"))
    return TaskRef(
        task_id=str(data.get("task_id") or ""),
        task_type=str(data.get("task_type") or ""),
        state=state,
        summary=str(data.get("summary") or ""),
        metadata=dict(data.get("metadata") or {}),
    )


def _task_signal_to_dict(signal: TaskSignal) -> dict[str, Any]:
    data = asdict(signal)
    data["artifacts"] = [_ref_to_dict(item) for item in signal.artifacts]
    return data


def _task_signal_message_content(signal: TaskSignal) -> str:
    """把 TaskSignal 转成可回灌给 Vision 模型的消息文本。

    主要逻辑：保留 task_id、task_type、signal_name 和 payload。若 payload 内有
    `text` 字段，先放在开头，便于模型优先读到任务结果摘要。
    参数：`signal` 为任务信号。
    返回值：一段中文前缀加 JSON 详情的文本。
    异常情况：payload 无法序列化时退化为字符串。
    """

    payload = signal.payload if isinstance(signal.payload, dict) else {"value": signal.payload}
    text = str(payload.get("text") or payload.get("message") or "").strip()
    detail = {
        "task_id": signal.task_id,
        "task_type": signal.task_type,
        "signal_name": signal.signal_name,
        "payload": payload,
    }
    try:
        detail_text = json.dumps(detail, ensure_ascii=False, default=str)
    except TypeError:
        detail_text = json.dumps({**detail, "payload": str(payload)}, ensure_ascii=False)
    if text:
        return f"任务结果：{text}\n{detail_text}"
    return f"任务结果：{detail_text}"


def _task_signal_from_dict(data: dict[str, Any]) -> TaskSignal:
    return TaskSignal(
        task_id=str(data.get("task_id") or ""),
        task_type=str(data.get("task_type") or ""),
        signal_name=str(data.get("signal_name") or ""),
        user_id=str(data.get("user_id") or ""),
        session_id=data.get("session_id"),
        payload=dict(data.get("payload") or {}),
        priority=str(data.get("priority") or "normal"),
        dedupe_key=data.get("dedupe_key"),
        ttl_seconds=int(data.get("ttl_seconds") or 0),
        requires_agent_decision=bool(data.get("requires_agent_decision", False)),
        allow_direct_notify=bool(data.get("allow_direct_notify", True)),
        artifacts=[_artifact_from_dict(item) for item in data.get("artifacts", []) if isinstance(item, dict)],
        created_at=float(data.get("created_at") or time.time()),
    )


def _ref_to_dict(ref: Any) -> dict[str, Any]:
    if hasattr(ref, "__dict__"):
        return dict(ref.__dict__)
    return dict(ref)


def _artifact_from_dict(data: dict[str, Any]) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=str(data.get("artifact_id") or new_id("artifact")),
        kind=str(data.get("kind") or "unknown"),
        uri=str(data.get("uri") or ""),
        metadata=dict(data.get("metadata") or {}),
    )
