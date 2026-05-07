from __future__ import annotations

import importlib
import inspect
import pkgutil
import time
from dataclasses import dataclass, field
from typing import Any

from audio_chat.asset import ArtifactRef
from audio_chat.errors import AudioChatError, ErrorCode

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
        return self._tasks[task_id]

    def append_event(self, event: TaskEvent) -> None:
        self._events.append(event)


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

        主要逻辑：记录 task event，并在配置输出服务时交给通知协调层。
        参数：`event` 为任务事件。
        返回值：无。
        异常情况：底层记录或输出失败时向上抛出。
        """
        if self.recorder and hasattr(self.recorder, "record_task_event"):
            self.recorder.record_task_event(event.session_id or event.task_id, event.__dict__)
        if self.output_service and hasattr(self.output_service, "notify_task_event"):
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
    ) -> None:
        self.registry = registry or TaskRegistry()
        self.store = store or TaskStore()
        self.state_machine = state_machine or TaskStateMachine()
        self.executor = executor or TaskExecutor()
        self.bridge = bridge or TaskEventBridge()

    def register(self, task_cls: type[BaseTask]) -> None:
        self.registry.register(task_cls)
