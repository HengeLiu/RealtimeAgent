"""backend-task-core 统一任务访问网关。"""

from __future__ import annotations

import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from backend_task_core.event_bus import TaskEventBus
from backend_task_core.models import TaskEvent, TaskRuntime, now_ms
from backend_task_core.registry import TaskRegistry
from backend_task_core.state_machine import TaskStateMachine
from backend_task_core.store import TaskContextStore
from infra.errors import ErrorCode, build_error


def generate_id(prefix: str) -> str:
    """生成统一前缀标识。"""

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class TaskGateway(ABC):
    """任务网关抽象接口。

    主要功能：
    1. 对上提供任务创建、查询、取消能力。
    2. 对外提供任务事件订阅入口。
    """

    @abstractmethod
    def create_task(
        self,
        *,
        task_type: str,
        session_id: str,
        device_id: str,
        input_data: dict[str, Any],
    ) -> TaskRuntime:
        """创建任务实例。"""

    @abstractmethod
    def query_task(self, task_id: str) -> TaskRuntime:
        """查询任务实例。"""

    @abstractmethod
    def cancel_task(self, task_id: str) -> TaskRuntime:
        """取消任务实例。"""

    @abstractmethod
    def subscribe_events(self, listener: Callable[[TaskEvent], None]) -> None:
        """订阅任务事件。"""

    @abstractmethod
    def shutdown(self) -> None:
        """关闭任务网关内部后台资源。"""


class InMemoryTaskGateway(TaskGateway):
    """带真实生命周期的内存任务网关。

    主要功能：
    1. 以内存存储承载任务实例，但不再只是静态字典。
    2. 对系统级后台任务提供创建、查询、取消与事件发布能力。
    3. 为后续切换更正式的 `TaskManager` 保留稳定 northbound 接口。
    """

    def __init__(self) -> None:
        self._registry = TaskRegistry()
        self._store = TaskContextStore()
        self._state_machine = TaskStateMachine()
        self._event_bus = TaskEventBus()
        self._lock = threading.Lock()

    def create_task(
        self,
        *,
        task_type: str,
        session_id: str,
        device_id: str,
        input_data: dict[str, Any],
    ) -> TaskRuntime:
        """创建任务实例。

        主要逻辑：
        1. 读取任务模板并校验输入。
        2. 先写入 `scheduled`，再推进到 `running`。
        """

        self._registry.get_spec(task_type)
        raise build_error(
            ErrorCode.TASK_NOT_FOUND,
            "当前内存任务网关未内建该任务类型，请通过 SDK 集成层提供任务实现",
            details={"task_type": task_type},
        )

    def query_task(self, task_id: str) -> TaskRuntime:
        """查询任务实例。"""

        runtime = self._store.get(task_id)
        if runtime is None:
            raise build_error(
                ErrorCode.TASK_NOT_FOUND,
                "目标任务不存在",
                details={"task_id": task_id},
            )
        runtime.updated_at_ms = now_ms()
        return self._store.update(runtime)

    def cancel_task(self, task_id: str) -> TaskRuntime:
        """取消任务实例。

        主要逻辑：
        1. 查询目标任务。
        2. 若仍处于活动态，则推进到 `cancelled`。
        3. 发布终态事件。
        """

        runtime = self.query_task(task_id)
        if runtime.state in {"failed", "timeout", "cancelled", "completed"}:
            return runtime

        runtime = self._transition_runtime(
            runtime=runtime,
            to_state="cancelled",
            phase="cancelled",
            result={"message": "任务已取消"},
        )
        self._publish_runtime_event(
            runtime=runtime,
            event_name="task.cancelled",
            priority="normal",
            requires_agent_decision=True,
            allow_direct_notify=True,
            payload={"message": "任务已取消"},
        )
        return runtime

    def subscribe_events(self, listener: Callable[[TaskEvent], None]) -> None:
        """订阅任务事件。"""

        self._event_bus.subscribe(listener)

    def shutdown(self) -> None:
        """关闭任务网关。

        主要逻辑：
        1. 当前内存网关没有后台线程，保留接口用于统一生命周期管理。
        """

        return None

    def _transition_runtime(
        self,
        *,
        runtime: TaskRuntime,
        to_state: str,
        phase: str,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> TaskRuntime:
        """推进任务状态并回写存储。"""

        self._state_machine.ensure_transition(from_state=runtime.state, to_state=to_state)
        runtime.state = to_state
        runtime.updated_at_ms = now_ms()
        runtime.context["phase"] = phase
        if result is not None:
            runtime.result = dict(result)
        if error is not None:
            runtime.error = dict(error)
        if to_state in {"completed", "cancelled", "failed", "timeout"}:
            runtime.completed_at_ms = runtime.updated_at_ms
        return self._store.update(runtime)

    def _publish_runtime_event(
        self,
        *,
        runtime: TaskRuntime,
        event_name: str,
        priority: str,
        requires_agent_decision: bool,
        allow_direct_notify: bool,
        payload: dict[str, Any],
    ) -> None:
        """发布一条任务事件。"""

        event = TaskEvent(
            event_id=generate_id("evt"),
            event_name=event_name,
            task_id=runtime.task_id,
            task_type=runtime.task_type,
            session_id=runtime.session_id,
            device_id=runtime.device_id,
            state=runtime.state,
            priority=priority,
            requires_agent_decision=requires_agent_decision,
            allow_direct_notify=allow_direct_notify,
            ts=now_ms(),
            payload=dict(payload),
        )
        self._event_bus.publish(event)
