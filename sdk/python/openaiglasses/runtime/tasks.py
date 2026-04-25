"""SDK 托管任务运行时。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from openaiglasses.capabilities import TaskContext, TaskEvent
from openaiglasses.capabilities.registry import CapabilityRegistry


def _new_task_id() -> str:
    """生成任务编号。"""

    return f"task_{uuid.uuid4().hex[:12]}"


@dataclass(slots=True)
class TaskRuntimeSnapshot:
    """SDK 托管任务快照。

    主要功能：
    1. 向开发者和测试代码暴露统一任务状态。
    2. 屏蔽任务对象、上下文缓存等内部实现细节。

    主要属性：
    1. `task_id`：任务实例编号。
    2. `task_type`：任务类型。
    3. `session_id`：所属会话编号。
    4. `device_id`：创建任务的设备编号。
    5. `state`：当前任务状态。
    6. `input_data`：任务输入参数。
    7. `data`：任务运行过程中的上下文数据。
    8. `result`：任务完成后的结构化结果。
    9. `error`：任务失败时的结构化错误。
    """

    task_id: str
    task_type: str
    session_id: str
    device_id: str
    state: str
    input_data: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


@dataclass(slots=True)
class _ManagedTaskRecord:
    """运行时内部任务记录。"""

    task_id: str
    task_type: str
    session_id: str
    device_id: str
    context: TaskContext
    error: dict[str, Any] | None = None

    def to_snapshot(self) -> TaskRuntimeSnapshot:
        """导出任务快照。"""

        return TaskRuntimeSnapshot(
            task_id=self.task_id,
            task_type=self.task_type,
            session_id=self.session_id,
            device_id=self.device_id,
            state=self.context.state,
            input_data=dict(self.context.input),
            data=dict(self.context.data),
            result=dict(self.context.result) if self.context.result is not None else None,
            error=dict(self.error) if self.error is not None else None,
        )


class TaskRuntimeManager:
    """SDK 托管任务运行时。

    主要功能：
    1. 根据注册表创建并驱动 `BaseTask`。
    2. 为 `DeviceGroupContext` 提供统一的任务创建、查询、取消和事件推进能力。
    3. 让示例能力和后续开发者能力不直接依赖旧的后台任务中心实现。
    """

    def __init__(self, *, registry: CapabilityRegistry, device_groups: Any) -> None:
        self._registry = registry
        self._device_groups = device_groups
        self._records: dict[str, _ManagedTaskRecord] = {}

    def bind_device_groups(self, device_groups: Any) -> None:
        """重新绑定设备组运行时。"""

        self._device_groups = device_groups

    def has_task(self, task_type: str) -> bool:
        """判断某类任务是否已注册。"""

        return self._registry.get_task(task_type) is not None

    def contains_task(self, task_id: str) -> bool:
        """判断某个任务是否存在。"""

        return task_id in self._records

    def create_task(
        self,
        *,
        task_type: str,
        device_id: str,
        session_id: str,
        input_data: dict[str, Any] | None = None,
    ) -> TaskRuntimeSnapshot:
        """创建并启动一个 SDK 托管任务。"""

        task = self._registry.get_task(task_type)
        if task is None:
            raise RuntimeError(f"未注册任务类型: {task_type}")

        task_id = _new_task_id()
        device_group = self._device_groups.create_context(
            device_id=device_id,
            session_id=session_id,
            task_id=task_id,
        )
        context = TaskContext(
            task_id=task_id,
            input=dict(input_data or {}),
            device_group=device_group,
        )
        record = _ManagedTaskRecord(
            task_id=task_id,
            task_type=task_type,
            session_id=session_id,
            device_id=device_id,
            context=context,
        )
        self._records[task_id] = record
        try:
            task.on_start(context)
        except Exception as exc:  # pragma: no cover - 当前阶段先保留最小失败兜底
            context.emit_state("failed")
            record.error = {
                "code": "task_start_failed",
                "message": str(exc),
            }
        return record.to_snapshot()

    def query_task(self, task_id: str) -> TaskRuntimeSnapshot:
        """查询任务快照。"""

        return self._require_record(task_id).to_snapshot()

    def cancel_task(self, task_id: str) -> TaskRuntimeSnapshot:
        """取消任务。"""

        record = self._require_record(task_id)
        task = self._registry.get_task(record.task_type)
        if task is None:
            raise RuntimeError(f"未注册任务类型: {record.task_type}")
        try:
            task.on_cancel(record.context)
        except Exception as exc:  # pragma: no cover - 当前阶段先保留最小失败兜底
            record.context.emit_state("failed")
            record.error = {
                "code": "task_cancel_failed",
                "message": str(exc),
            }
        return record.to_snapshot()

    def dispatch_event(
        self,
        *,
        task_id: str,
        event_name: str,
        payload: dict[str, Any] | None = None,
        source: str = "system",
    ) -> TaskRuntimeSnapshot:
        """向任务派发一个结构化事件。"""

        record = self._require_record(task_id)
        task = self._registry.get_task(record.task_type)
        if task is None:
            raise RuntimeError(f"未注册任务类型: {record.task_type}")
        event = TaskEvent(
            name=event_name,
            payload=dict(payload or {}),
            source=source,
        )
        try:
            task.on_event(record.context, event)
        except Exception as exc:  # pragma: no cover - 当前阶段先保留最小失败兜底
            record.context.emit_state("failed")
            record.error = {
                "code": "task_event_failed",
                "message": str(exc),
            }
        return record.to_snapshot()

    def list_tasks(self) -> list[TaskRuntimeSnapshot]:
        """列出当前全部任务快照。"""

        return [record.to_snapshot() for record in self._records.values()]

    def _require_record(self, task_id: str) -> _ManagedTaskRecord:
        """读取内部任务记录。"""

        record = self._records.get(task_id)
        if record is None:
            raise RuntimeError(f"任务不存在: {task_id}")
        return record


class BackendTaskGatewayAdapter:
    """`backend_task_core.TaskGateway` 到 SDK 任务视图的桥接器。

    主要功能：
    1. 复用现有服务端后台任务网关。
    2. 向 `DeviceGroupRuntime` 暴露与 SDK 托管任务运行时兼容的最小接口。
    3. 让真实 `ControlRuntime` 中的 `DeviceGroupContext.create_task()` 可以直接进入旧任务中心。
    """

    def __init__(self, *, task_gateway: Any) -> None:
        self._task_gateway = task_gateway

    def create_task(
        self,
        *,
        task_type: str,
        device_id: str,
        session_id: str,
        input_data: dict[str, Any] | None = None,
    ) -> TaskRuntimeSnapshot:
        """通过旧任务网关创建任务。"""

        runtime = self._task_gateway.create_task(
            task_type=task_type,
            session_id=session_id,
            device_id=device_id,
            input_data=dict(input_data or {}),
        )
        return self._to_snapshot(runtime)

    def query_task(self, task_id: str) -> TaskRuntimeSnapshot:
        """通过旧任务网关查询任务。"""

        return self._to_snapshot(self._task_gateway.query_task(task_id))

    def cancel_task(self, task_id: str) -> TaskRuntimeSnapshot:
        """通过旧任务网关取消任务。"""

        return self._to_snapshot(self._task_gateway.cancel_task(task_id))

    @staticmethod
    def _to_snapshot(runtime: Any) -> TaskRuntimeSnapshot:
        """把旧任务运行态转换成 SDK 快照。"""

        context = getattr(runtime, "context", None)
        result = getattr(runtime, "result", None)
        error = getattr(runtime, "error", None)
        return TaskRuntimeSnapshot(
            task_id=str(getattr(runtime, "task_id")),
            task_type=str(getattr(runtime, "task_type")),
            session_id=str(getattr(runtime, "session_id")),
            device_id=str(getattr(runtime, "device_id")),
            state=str(getattr(runtime, "state")),
            input_data=dict(getattr(runtime, "input", {}) or {}),
            data=dict(context) if isinstance(context, dict) else {},
            result=dict(result) if isinstance(result, dict) else None,
            error=dict(error) if isinstance(error, dict) else None,
        )
