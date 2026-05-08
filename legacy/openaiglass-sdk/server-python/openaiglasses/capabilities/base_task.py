"""Task 扩展基类。"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(slots=True)
class TaskEvent:
    """任务事件。

    主要功能：
    1. 表示任务运行期间收到的外部事件或系统事件。
    2. 让长任务通过结构化事件推进状态。

    主要属性：
    1. `name`：事件名称。
    2. `payload`：事件载荷。
    3. `source`：事件来源。
    """

    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "system"


@dataclass(slots=True)
class TaskContext:
    """任务上下文。

    主要功能：
    1. 向开发者任务提供设备组、输入、状态和结果入口。
    2. 避免开发者直接管理任务存储和事件回流。

    主要属性：
    1. `task_id`：任务实例编号。
    2. `input`：任务输入参数。
    3. `device_group`：设备组上下文。
    """

    task_id: str
    input: dict[str, Any]
    device_group: Any
    scheduler: Callable[..., dict[str, Any]] | None = None
    state: str = "created"
    data: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None

    def emit_state(self, state: str, data: dict[str, Any] | None = None) -> None:
        """更新任务状态。

        参数：
        1. `state`：新的任务状态。
        2. `data`：需要合并进任务上下文的数据。

        返回值：
        1. 无。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        self.state = state
        if data:
            self.data.update(data)

    def update(self, data: dict[str, Any]) -> None:
        """更新任务上下文数据。

        参数：
        1. `data`：需要合并的数据。

        返回值：
        1. 无。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        self.data.update(data)

    def complete(self, result: dict[str, Any] | None = None) -> None:
        """完成任务。

        参数：
        1. `result`：任务最终结果。

        返回值：
        1. 无。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        self.state = "completed"
        self.result = result or {}

    def schedule_event(
        self,
        *,
        delay_ms: int,
        event_name: str,
        payload: dict[str, Any] | None = None,
        source: str = "scheduler",
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """安排一次延迟任务事件。

        功能：
        1. 让业务 Task 使用 SDK 托管的通用定时调度能力。
        2. 到点后 SDK 会把事件重新派发给当前 Task 的 `on_event(...)`。
        3. 任务已经进入终态时，到点事件会被 SDK 安全忽略。

        主要逻辑：
        1. 校验延迟时间和事件名称。
        2. 委托任务运行时注册一次性定时器。
        3. 返回可观测的调度记录，便于测试和日志排查。

        参数：
        1. `delay_ms`：延迟毫秒数，必须大于 0。
        2. `event_name`：到点后派发给 Task 的事件名。
        3. `payload`：事件载荷。
        4. `source`：事件来源，默认是 `scheduler`。
        5. `event_id`：可选幂等事件编号。

        返回值：
        1. 调度记录字典，包含 `schedule_id/task_id/event_name/due_at_ms`。

        异常情况：
        1. 当前上下文未绑定调度器时抛出 `RuntimeError`。
        2. 延迟时间或事件名称非法时由运行时抛出 `RuntimeError`。
        """

        if self.scheduler is None:
            raise RuntimeError("当前 TaskContext 未绑定 SDK 调度器")
        return self.scheduler(
            task_id=self.task_id,
            delay_ms=delay_ms,
            event_name=event_name,
            payload=dict(payload or {}),
            source=source,
            event_id=event_id,
        )


class BaseTask(ABC):
    """长生命周期任务基类。

    主要功能：
    1. 让开发者定义持续运行的业务能力。
    2. 由 SDK 托管任务生命周期、事件回流和设备组能力。

    主要方法：
    1. `on_start`：任务启动。
    2. `on_event`：任务收到事件。
    3. `on_cancel`：任务取消。
    """

    task_type: str = ""
    description: str = ""
    terminal_event_requires_agent_decision: bool = False
    terminal_event_allow_direct_notify: bool = True
    terminal_event_priority: str = "normal"

    def on_start(self, context: TaskContext) -> None:
        """任务启动回调。

        参数：
        1. `context`：任务上下文。

        返回值：
        1. 无。

        异常情况：
        1. 子类可以按业务需要抛出异常，由 SDK 统一记录。
        """

    def on_event(self, context: TaskContext, event: TaskEvent) -> None:
        """任务事件回调。

        参数：
        1. `context`：任务上下文。
        2. `event`：任务事件。

        返回值：
        1. 无。

        异常情况：
        1. 子类可以按业务需要抛出异常，由 SDK 统一记录。
        """

    def on_cancel(self, context: TaskContext) -> None:
        """任务取消回调。

        参数：
        1. `context`：任务上下文。

        返回值：
        1. 无。

        异常情况：
        1. 子类可以按业务需要抛出异常，由 SDK 统一记录。
        """

        context.emit_state("cancelled")
