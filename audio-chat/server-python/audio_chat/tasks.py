from __future__ import annotations

import importlib
import inspect
import pkgutil
import time
from dataclasses import dataclass, field, replace
from typing import Any

from audio_chat.asset import ArtifactRef
from audio_chat.errors import AudioChatError, ErrorCode
from audio_chat.protocol import new_id

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
    metadata: dict = field(default_factory=dict)


class BaseTask:
    """业务 Task 基类。

    主要功能：定义长任务稳定扩展面，自动发现只注册继承该类的具体子类。
    """

    task_type: str = ""
    description: str = ""

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

    主要功能：保存 TaskRef 和事件，后续可替换为持久化实现。
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
            self.recorder.record_task_event(event.session_id or event.task_id, event.__dict__)
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
        device_context_factory: Any = None,
    ) -> None:
        self.registry = registry or TaskRegistry()
        self.store = store or TaskStore()
        self.state_machine = state_machine or TaskStateMachine()
        self.executor = executor or TaskExecutor()
        self.bridge = bridge or TaskEventBridge()
        self.device_context_factory = device_context_factory
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

        return self.store.get(task_id)

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
        task_id = new_id("task")
        ref = TaskRef(
            task_id=task_id,
            task_type=task_type,
            state="scheduled",
            summary=summary,
            metadata={"user_id": user_id, "session_id": session_id, "input": dict(input_data or {})},
        )
        self.store.put(ref)
        task = task_cls()
        self._instances[task_id] = task
        ref = self._transition(ref, "running")
        context = self._context(user_id=user_id, session_id=session_id, ref=ref)
        try:
            await self.executor.start(task, context)
        except Exception as exc:
            self._transition(ref, "failed")
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
        return ref

    async def handle_event(self, event: TaskEvent) -> TaskRef:
        """把外部 TaskEvent 送回对应 Task 实例。

        主要逻辑：先记录事件，再调用 Task.on_event；是否通知或回流 Agent 由 bridge
        根据事件字段决定。
        参数：`event` 为任务事件。
        返回值：当前 TaskRef。
        异常情况：任务不存在时抛出 `AudioChatError`。
        """

        ref = self.store.get(event.task_id)
        task = self._instances.get(event.task_id)
        self.emit_event(event)
        if task is not None:
            await self.executor.event(
                task,
                self._context(user_id=event.user_id, session_id=event.session_id, ref=ref),
                event,
            )
        return self.store.get(event.task_id)

    async def cancel(self, task_id: str, *, reason: str = "cancelled") -> TaskRef:
        """取消任务。

        主要逻辑：执行 Task.on_cancel，状态流转到 cancelled，并记录 `task.cancelled`。
        参数：`task_id` 为任务 ID，`reason` 为取消原因。
        返回值：取消后的 TaskRef。
        异常情况：非法状态流转或任务不存在时抛出 `AudioChatError`。
        """

        ref = self.store.get(task_id)
        task = self._instances.get(task_id)
        if task is not None:
            await self.executor.cancel(
                task,
                self._context(user_id=str(ref.metadata.get("user_id") or ""), session_id=None, ref=ref),
            )
        ref = self._transition(ref, "cancelled")
        self.emit_event(
            TaskEvent(
                task_id=ref.task_id,
                task_type=ref.task_type,
                event_name="task.cancelled",
                user_id=str(ref.metadata.get("user_id") or ""),
                payload={"reason": reason},
                allow_direct_notify=True,
            )
        )
        return ref

    def emit_event(self, event: TaskEvent) -> None:
        """记录并桥接任务事件。"""

        self.store.append_event(event)
        self.bridge.handle_event(event)

    def _transition(self, ref: TaskRef, target: str) -> TaskRef:
        state = self.state_machine.transition(ref.state, target)
        updated = replace(ref, state=state)
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
            metadata=dict(ref.metadata),
        )
