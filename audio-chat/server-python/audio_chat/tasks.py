from __future__ import annotations

import importlib
import inspect
import asyncio
import json
import pkgutil
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from audio_chat.asset import ArtifactRef
from audio_chat.errors import AudioChatError, ErrorCode
from audio_chat.protocol import new_id

TERMINAL_TASK_STATES = {"completed", "cancelled", "failed", "timeout"}

TASK_STATES = (
    "scheduled",
    "running",
    "waiting_external",
    "completed",
    "cancelled",
    "failed",
    "timeout",
)

TASK_TRANSITIONS = {
    ("scheduled", "running"),
    ("scheduled", "failed"),
    ("scheduled", "timeout"),
    ("running", "waiting_external"),
    ("waiting_external", "running"),
    ("waiting_external", "completed"),
    ("running", "completed"),
    ("running", "cancelled"),
    ("scheduled", "cancelled"),
    ("running", "failed"),
    ("waiting_external", "failed"),
    ("running", "timeout"),
    ("waiting_external", "timeout"),
}


@dataclass(frozen=True)
class TaskSpec:
    """Task 运行规格。

    主要功能：把 Task 类型、版本、超时、取消能力和用户级并发限制收敛成稳定描述。
    主要属性：`task_type/version/timeout_seconds/cancel_supported/max_running_per_user`。
    """

    task_type: str
    version: str = "v1"
    timeout_seconds: float | None = None
    cancel_supported: bool = True
    max_running_per_user: int | None = None


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
class TaskEvent:
    """任务生命周期事件。

    主要功能：承载任务状态回流、通知和 Agent 决策所需的结构化数据。
    主要属性：`priority`、`dedupe_key`、`ttl_seconds` 可被 NotificationCoordinator 使用。
    """

    task_id: str
    task_type: str
    event_name: str
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


@dataclass
class TaskContext:
    """Task 执行上下文。

    主要功能：由 TaskEngine 注入设备上下文、任务引用和事件桥，Task 不直接操作消息或播放。
    """

    user_id: str
    session_id: str | None
    task_ref: TaskRef
    devices: Any = None
    bridge: "TaskEventBridge | None" = None
    engine: "TaskEngine | None" = None
    metadata: dict = field(default_factory=dict)

    async def complete(self, payload: dict | None = None, *, summary: str = "") -> TaskRef:
        """把当前任务标记为完成。

        主要逻辑：委托 TaskEngine 完成状态流转并写入 `task.completed` 事件。
        参数：`payload` 为完成事件载荷，`summary` 为任务摘要。
        返回值：完成后的 `TaskRef`。
        异常情况：上下文未绑定 TaskEngine 时抛出协议错误。
        """

        if self.engine is None:
            raise AudioChatError("task context has no engine", code=ErrorCode.PROTOCOL_ERROR)
        return self.engine.complete(self.task_ref.task_id, payload=payload or {}, summary=summary)

    async def fail(self, message: str, *, payload: dict | None = None) -> TaskRef:
        """把当前任务标记为失败。"""

        if self.engine is None:
            raise AudioChatError("task context has no engine", code=ErrorCode.PROTOCOL_ERROR)
        return self.engine.fail(self.task_ref.task_id, message=message, payload=payload or {})

    async def schedule_event(
        self,
        event_name: str,
        *,
        payload: dict | None = None,
        delay_seconds: float = 0,
        priority: str = "normal",
        requires_agent_decision: bool = False,
        allow_direct_notify: bool = False,
    ) -> TaskRef | None:
        """调度一个任务事件。

        功能：
        1. 给业务 Task 提供稳定的延时事件入口，避免业务代码自建线程或定时器。
        2. 延时到达后把事件重新送回 TaskEngine，使 `on_event()` 能处理到点、超时前提醒等状态。

        主要逻辑：
        1. `delay_seconds` 大于 0 时先异步等待。
        2. 构造 `TaskEvent`，复用当前任务编号、任务类型、用户和会话。
        3. 优先通过 TaskEngine 回流；没有绑定 engine 时退化为只通过 bridge 记录。

        参数：
        1. `event_name`：任务事件名，例如 `timer.due`。
        2. `payload`：事件载荷。
        3. `delay_seconds`：延时秒数；小于等于 0 表示立即回流。
        4. `priority`：事件优先级。
        5. `requires_agent_decision`：是否需要进入 Agent 上下文同步。
        6. `allow_direct_notify`：是否允许事件桥直接转成通知输出。

        返回值：
        1. 绑定 TaskEngine 时返回事件处理后的 `TaskRef`。
        2. 仅绑定 bridge 时返回 `None`。

        异常情况：
        1. 事件处理失败时由 TaskEngine 或 bridge 抛出结构化异常。
        """

        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        event = TaskEvent(
            task_id=self.task_ref.task_id,
            task_type=self.task_ref.task_type,
            event_name=event_name,
            user_id=self.user_id,
            session_id=self.session_id,
            payload=dict(payload or {}),
            priority=priority,
            requires_agent_decision=requires_agent_decision,
            allow_direct_notify=allow_direct_notify,
        )
        if self.engine is not None:
            return await self.engine.handle_event(event)
        if self.bridge is not None:
            self.bridge.handle_event(event)
        return None


class BaseTask:
    """业务 Task 基类。

    主要功能：定义长任务稳定扩展面，自动发现只注册继承该类的具体子类。
    """

    task_type: str = ""
    description: str = ""
    version: str = "v1"
    timeout_seconds: float | None = None
    cancel_supported: bool = True
    max_running_per_user: int | None = None

    @classmethod
    def spec(cls) -> TaskSpec:
        """返回 Task 运行规格。

        主要逻辑：从类属性读取稳定字段，注册表和调度器统一使用该描述。
        参数：无。
        返回值：`TaskSpec`。
        异常情况：`task_type` 为空时由注册表负责报错。
        """

        return TaskSpec(
            task_type=cls.task_type or cls.__name__,
            version=str(getattr(cls, "version", "v1") or "v1"),
            timeout_seconds=getattr(cls, "timeout_seconds", None),
            cancel_supported=bool(getattr(cls, "cancel_supported", True)),
            max_running_per_user=getattr(cls, "max_running_per_user", None),
        )

    async def on_start(self, context: TaskContext) -> None:
        """任务启动回调。

        主要逻辑：基类只声明接口，子类按需覆盖。
        参数：`context` 为 SDK 注入上下文。
        返回值：无。
        异常情况：无。
        """
        return None

    async def on_event(self, context: TaskContext, event: TaskEvent) -> None:
        return None

    async def on_cancel(self, context: TaskContext) -> None:
        return None


class TaskStateMachine:
    """任务状态机。

    主要功能：约束任务状态只能沿设计文档允许路径流转。
    """

    def transition(self, current: str, target: str) -> str:
        if current == target:
            return target
        if (current, target) not in TASK_TRANSITIONS:
            raise AudioChatError(f"invalid task transition: {current}->{target}", code=ErrorCode.PROTOCOL_ERROR)
        return target


class TaskStore:
    """进程内任务存储。

    主要功能：保存 TaskRef 和事件，可作为持久化 store 的内存基类。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRef] = {}
        self._events: list[TaskEvent] = []

    def put(self, ref: TaskRef) -> None:
        self._tasks[ref.task_id] = ref

    def get(self, task_id: str) -> TaskRef:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise AudioChatError(f"unknown task: {task_id}", code=ErrorCode.NOT_FOUND) from exc

    def append_event(self, event: TaskEvent) -> None:
        self._events.append(event)

    def events_for_task(self, task_id: str) -> list[TaskEvent]:
        """返回某个任务的事件列表。"""

        return [event for event in self._events if event.task_id == task_id]

    def list_tasks(self) -> list[TaskRef]:
        """列出全部任务快照。"""

        return list(self._tasks.values())

    def list_unfinished(self) -> list[TaskRef]:
        """列出未进入终态的任务快照。"""

        return [ref for ref in self._tasks.values() if ref.state not in TERMINAL_TASK_STATES]


class JsonlTaskStore(TaskStore):
    """JSONL 持久化任务存储。

    主要功能：把 TaskRef 快照和 TaskEvent 追加写入 jsonl，重启后可重放恢复。
    主要属性：`root` 为存储目录，`tasks_path/events_path` 为落地文件。
    """

    def __init__(self, root: str | Path) -> None:
        super().__init__()
        self.root = Path(root)
        self.tasks_path = self.root / "tasks.jsonl"
        self.events_path = self.root / "task-events.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self._load()

    def put(self, ref: TaskRef) -> None:
        super().put(ref)
        self._append_jsonl(self.tasks_path, {"record_type": "task.snapshot", "task": _task_ref_to_dict(ref)})

    def append_event(self, event: TaskEvent) -> None:
        super().append_event(event)
        self._append_jsonl(self.events_path, {"record_type": "task.event", "event": _task_event_to_dict(event)})

    def _load(self) -> None:
        """重放 jsonl 文件，恢复任务和事件内存索引。"""

        if self.tasks_path.exists():
            for record in _read_jsonl(self.tasks_path):
                task_data = record.get("task") if isinstance(record, dict) else None
                if isinstance(task_data, dict):
                    ref = _task_ref_from_dict(task_data)
                    self._tasks[ref.task_id] = ref
        if self.events_path.exists():
            for record in _read_jsonl(self.events_path):
                event_data = record.get("event") if isinstance(record, dict) else None
                if isinstance(event_data, dict):
                    self._events.append(_task_event_from_dict(event_data))

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
        task_type = task_cls.task_type or task_cls.__name__
        if not task_type:
            raise AudioChatError("task_type is required", code=ErrorCode.INVALID_ARGUMENT)
        if task_type in self._tasks:
            raise AudioChatError(f"duplicate task_type: {task_type}", code=ErrorCode.PROTOCOL_ERROR)
        self._tasks[task_type] = task_cls

    def get(self, task_type: str) -> type[BaseTask]:
        if task_type not in self._tasks:
            raise AudioChatError(f"unknown task: {task_type}", code=ErrorCode.NOT_FOUND)
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
                    task_type = obj.task_type or obj.__name__
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
                            raise AudioChatError(error["error"], code=ErrorCode.PROTOCOL_ERROR, details=error)
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
            raise AudioChatError("task discovery import failed", code=ErrorCode.PROTOCOL_ERROR, details=error) from exc
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


class TaskEventBridge:
    """TaskEvent 桥接层。

    主要功能：Task 不直接写消息、不直接播放、不直接重入 Agent；事件统一通过本层回流。
    """

    def __init__(self, *, recorder: Any = None, output_service: Any = None) -> None:
        self.recorder = recorder
        self.output_service = output_service

    def handle_event(self, event: TaskEvent) -> None:
        """处理任务事件。

        主要逻辑：记录 task event；需要 Agent 决策的事件写入上下文同步记录；允许直
        发的事件才交给通知协调层。
        参数：`event` 为任务事件。
        返回值：无。
        异常情况：底层记录或输出失败时向上抛出。
        """
        if self.recorder and hasattr(self.recorder, "record_task_event"):
            self.recorder.record_task_event(event.session_id or event.task_id, _task_event_to_dict(event))
        if event.requires_agent_decision and self.recorder and hasattr(self.recorder, "record_agent_event"):
            self.recorder.record_agent_event(
                event.session_id or event.task_id,
                {
                    "event": "task.requires_agent_context_sync",
                    "task_id": event.task_id,
                    "task_type": event.task_type,
                    "task_event_name": event.event_name,
                    "payload": event.payload,
                },
            )
        if event.allow_direct_notify and self.output_service and hasattr(self.output_service, "notify_task_event"):
            self.output_service.notify_task_event(event)

    def convert_event_to_agent_turn(self, event: TaskEvent) -> dict:
        """转换任务事件为 Agent 可读轮次。

        主要逻辑：保留 task_id、task_type、event_name 和 payload。
        参数：`event` 为任务事件。
        返回值：字典。
        异常情况：无。
        """
        return {
            "role": "tool",
            "task_id": event.task_id,
            "task_type": event.task_type,
            "event_name": event.event_name,
            "payload": event.payload,
        }


class TaskExecutor:
    """Task 执行器。"""

    async def start(self, task: BaseTask, context: TaskContext) -> None:
        await task.on_start(context)

    async def event(self, task: BaseTask, context: TaskContext, event: TaskEvent) -> None:
        await task.on_event(context, event)

    async def cancel(self, task: BaseTask, context: TaskContext) -> None:
        await task.on_cancel(context)


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

    主要功能：创建任务引用、执行任务启动回调，并通过 TaskEventBridge 回流事件。
    """

    def __init__(
        self,
        *,
        registry: TaskRegistry | None = None,
        store: TaskStore | None = None,
        state_machine: TaskStateMachine | None = None,
        executor: TaskExecutor | None = None,
        bridge: TaskEventBridge | None = None,
        scheduler: TaskScheduler | None = None,
        device_context_factory: Any = None,
        max_running_per_user: int = 16,
    ) -> None:
        self.registry = registry or TaskRegistry()
        self.store = store or TaskStore()
        self.state_machine = state_machine or TaskStateMachine()
        self.executor = executor or TaskExecutor()
        self.bridge = bridge or TaskEventBridge()
        self.scheduler = scheduler or TaskScheduler()
        self.device_context_factory = device_context_factory
        self.max_running_per_user = max_running_per_user
        self._instances: dict[str, BaseTask] = {}

    def register(self, task_cls: type[BaseTask]) -> None:
        self.registry.register(task_cls)

    def query(self, task_id: str) -> TaskRef:
        """查询任务引用。

        主要逻辑：从 TaskStore 返回稳定 TaskRef，不暴露 Task 实例。
        参数：`task_id` 为任务 ID。
        返回值：TaskRef。
        异常情况：任务不存在时抛出 `AudioChatError`。
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
            except AudioChatError:
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

        主要逻辑：创建 `scheduled` 引用，立即流转为 `running`，执行 Task.on_start，
        并通过 TaskEventBridge 记录启动或失败事件。
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
            state="scheduled",
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
                "started_at": None,
                "updated_at": now,
            },
        )
        self.store.put(ref)
        task = task_cls()
        self._instances[task_id] = task
        ref = self._transition(ref, "running", metadata={"started_at": time.time()})
        self.emit_event(
            TaskEvent(
                task_id=task_id,
                task_type=task_type,
                event_name="task.started",
                user_id=user_id,
                session_id=session_id,
                payload={"state": ref.state, "input": dict(input_data or {})},
                allow_direct_notify=False,
            )
        )
        context = self._context(user_id=user_id, session_id=session_id, ref=ref)
        try:
            await self.executor.start(task, context)
        except Exception as exc:
            self._transition(ref, "failed", metadata={"error": str(exc)})
            self.emit_event(
                TaskEvent(
                    task_id=task_id,
                    task_type=task_type,
                    event_name="task.failed",
                    user_id=user_id,
                    session_id=session_id,
                    payload={"message": str(exc)},
                    priority="high",
                    requires_agent_decision=True,
                    allow_direct_notify=True,
                )
            )
            raise
        return self.query(task_id)

    async def handle_event(self, event: TaskEvent) -> TaskRef:
        """把外部 TaskEvent 送回对应 Task 实例。

        主要逻辑：先记录事件，再调用 Task.on_event；是否通知或回流 Agent 由 bridge
        根据事件字段决定。
        参数：`event` 为任务事件。
        返回值：当前 TaskRef。
        异常情况：任务不存在时抛出 `AudioChatError`。
        """

        ref = self.query(event.task_id)
        task = self._instances.get(event.task_id)
        self.emit_event(event)
        if task is not None:
            await self.executor.event(
                task,
                self._context(user_id=event.user_id, session_id=event.session_id, ref=ref),
                event,
            )
        return self.query(event.task_id)

    async def cancel(self, task_id: str, *, reason: str = "cancelled") -> TaskRef:
        """取消任务。

        主要逻辑：执行 Task.on_cancel，状态流转到 cancelled，并记录 `task.cancelled`。
        参数：`task_id` 为任务 ID，`reason` 为取消原因。
        返回值：取消后的 TaskRef。
        异常情况：非法状态流转或任务不存在时抛出 `AudioChatError`。
        """

        ref = self.query(task_id)
        if ref.state in TERMINAL_TASK_STATES:
            return ref
        if ref.metadata.get("cancel_supported") is False:
            raise AudioChatError(f"task does not support cancel: {task_id}", code=ErrorCode.PROTOCOL_ERROR)
        task = self._instances.get(task_id)
        if task is not None:
            await self.executor.cancel(
                task,
                self._context(
                    user_id=str(ref.metadata.get("user_id") or ""),
                    session_id=str(ref.metadata.get("session_id") or "") or None,
                    ref=ref,
                ),
            )
        ref = self._transition(ref, "cancelled")
        self.emit_event(
            TaskEvent(
                task_id=ref.task_id,
                task_type=ref.task_type,
                event_name="task.cancelled",
                user_id=str(ref.metadata.get("user_id") or ""),
                session_id=str(ref.metadata.get("session_id") or "") or None,
                payload={"reason": reason},
                allow_direct_notify=True,
            )
        )
        return ref

    def complete(self, task_id: str, *, payload: dict | None = None, summary: str = "") -> TaskRef:
        """完成任务并写入 `task.completed` 事件。"""

        ref = self.query(task_id)
        if ref.state in TERMINAL_TASK_STATES:
            return ref
        ref = self._transition(ref, "completed", summary=summary or ref.summary)
        self.emit_event(
            TaskEvent(
                task_id=ref.task_id,
                task_type=ref.task_type,
                event_name="task.completed",
                user_id=str(ref.metadata.get("user_id") or ""),
                session_id=str(ref.metadata.get("session_id") or "") or None,
                payload={"state": ref.state, **dict(payload or {})},
                allow_direct_notify=False,
            )
        )
        return ref

    def fail(self, task_id: str, *, message: str, payload: dict | None = None) -> TaskRef:
        """失败任务并写入 `task.failed` 事件。"""

        ref = self.query(task_id)
        if ref.state in TERMINAL_TASK_STATES:
            return ref
        ref = self._transition(ref, "failed", metadata={"error": message})
        self.emit_event(
            TaskEvent(
                task_id=ref.task_id,
                task_type=ref.task_type,
                event_name="task.failed",
                user_id=str(ref.metadata.get("user_id") or ""),
                session_id=str(ref.metadata.get("session_id") or "") or None,
                payload={"message": message, **dict(payload or {})},
                priority="high",
                requires_agent_decision=True,
                allow_direct_notify=True,
            )
        )
        return ref

    def emit_event(self, event: TaskEvent) -> None:
        """记录并桥接任务事件。"""

        self.store.append_event(event)
        self.bridge.handle_event(event)

    def _transition(self, ref: TaskRef, target: str, *, metadata: dict | None = None, summary: str | None = None) -> TaskRef:
        state = self.state_machine.transition(ref.state, target)
        merged_metadata = {**dict(ref.metadata), **dict(metadata or {}), "updated_at": time.time()}
        updated = replace(ref, state=state, metadata=merged_metadata, summary=ref.summary if summary is None else summary)
        self.store.put(updated)
        return updated

    def _context(self, *, user_id: str, session_id: str | None, ref: TaskRef) -> TaskContext:
        devices = self.device_context_factory(user_id=user_id) if callable(self.device_context_factory) else None
        return TaskContext(
            user_id=user_id,
            session_id=session_id,
            task_ref=ref,
            devices=devices,
            bridge=self.bridge,
            engine=self,
            metadata=dict(ref.metadata),
        )

    def _expire_if_needed(self, ref: TaskRef) -> TaskRef:
        if not self.scheduler.expired(ref):
            return ref
        ref = self._transition(ref, "timeout", metadata={"timeout_at": time.time()})
        self.emit_event(
            TaskEvent(
                task_id=ref.task_id,
                task_type=ref.task_type,
                event_name="task.timeout",
                user_id=str(ref.metadata.get("user_id") or ""),
                session_id=str(ref.metadata.get("session_id") or "") or None,
                payload={"timeout_seconds": ref.metadata.get("timeout_seconds")},
                priority="high",
                requires_agent_decision=True,
                allow_direct_notify=True,
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
            raise AudioChatError(
                "task running concurrency exceeded",
                code=ErrorCode.PROTOCOL_ERROR,
                details={"user_id": user_id, "limit": limit, "task_type": spec.task_type},
            )


def _resolve_timeout_seconds(spec: TaskSpec, input_data: dict) -> float | None:
    raw = input_data.get("timeout_seconds", spec.timeout_seconds)
    if raw is None:
        return None
    value = float(raw)
    return value if value > 0 else None


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
    return TaskRef(
        task_id=str(data.get("task_id") or ""),
        task_type=str(data.get("task_type") or ""),
        state=str(data.get("state") or "scheduled"),
        summary=str(data.get("summary") or ""),
        metadata=dict(data.get("metadata") or {}),
    )


def _task_event_to_dict(event: TaskEvent) -> dict[str, Any]:
    data = asdict(event)
    data["artifacts"] = [_ref_to_dict(item) for item in event.artifacts]
    return data


def _task_event_from_dict(data: dict[str, Any]) -> TaskEvent:
    return TaskEvent(
        task_id=str(data.get("task_id") or ""),
        task_type=str(data.get("task_type") or ""),
        event_name=str(data.get("event_name") or ""),
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
