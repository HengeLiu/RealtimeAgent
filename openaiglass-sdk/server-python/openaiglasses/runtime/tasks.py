"""SDK 托管任务运行时。"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openaiglasses.capabilities import TaskContext, TaskEvent
from openaiglasses.capabilities.registry import CapabilityRegistry


def _new_task_id() -> str:
    """生成任务编号。"""

    return f"task_{uuid.uuid4().hex[:12]}"


@dataclass(slots=True)
class TaskRuntimeEventLog:
    """SDK 托管任务事件日志。

    主要功能：
    1. 记录任务创建、启动、外部事件、完成、失败、取消和超时。
    2. 为后续持久化恢复、回放测试和运行态诊断提供最小事件历史。

    主要属性：
    1. `event_name`：事件名称。
    2. `state`：事件发生后的任务状态。
    3. `source`：事件来源。
    4. `payload`：事件结构化内容。
    """

    event_id: str
    event_name: str
    state: str
    source: str = "sdk"
    payload: dict[str, Any] = field(default_factory=dict)
    ts_ms: int = field(default_factory=lambda: int(__import__("time").time() * 1000))


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
    created_at_ms: int = 0
    updated_at_ms: int = 0
    started_at_ms: int | None = None
    completed_at_ms: int | None = None
    timeout_ms: int | None = None
    deadline_at_ms: int | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class _ManagedTaskRecord:
    """运行时内部任务记录。"""

    task_id: str
    task_type: str
    session_id: str
    device_id: str
    context: TaskContext
    error: dict[str, Any] | None = None
    created_at_ms: int = field(default_factory=lambda: int(__import__("time").time() * 1000))
    updated_at_ms: int = field(default_factory=lambda: int(__import__("time").time() * 1000))
    started_at_ms: int | None = None
    completed_at_ms: int | None = None
    timeout_ms: int | None = None
    deadline_at_ms: int | None = None
    events: list[TaskRuntimeEventLog] = field(default_factory=list)

    def to_snapshot(self) -> TaskRuntimeSnapshot:
        """导出任务快照。"""

        self.updated_at_ms = _now_ms()
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
            created_at_ms=self.created_at_ms,
            updated_at_ms=self.updated_at_ms,
            started_at_ms=self.started_at_ms,
            completed_at_ms=self.completed_at_ms,
            timeout_ms=self.timeout_ms,
            deadline_at_ms=self.deadline_at_ms,
            events=[asdict(event) for event in self.events],
        )

    def append_event(
        self,
        *,
        event_name: str,
        source: str = "sdk",
        payload: dict[str, Any] | None = None,
    ) -> None:
        """追加任务事件日志并刷新更新时间。"""

        self.updated_at_ms = _now_ms()
        self.events.append(
            TaskRuntimeEventLog(
                event_id=f"sdk_task_evt_{uuid.uuid4().hex[:12]}",
                event_name=event_name,
                state=self.context.state,
                source=source,
                payload=dict(payload or {}),
                ts_ms=self.updated_at_ms,
            )
        )


def _now_ms() -> int:
    """返回当前毫秒时间戳。"""

    import time

    return int(time.time() * 1000)


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
        created_at_ms = _now_ms()
        timeout_ms = self._parse_timeout_ms(input_data or {})
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
            created_at_ms=created_at_ms,
            updated_at_ms=created_at_ms,
            timeout_ms=timeout_ms,
            deadline_at_ms=(created_at_ms + timeout_ms) if timeout_ms is not None else None,
        )
        self._records[task_id] = record
        record.append_event(
            event_name="task.created",
            payload={"task_type": task_type, "input": dict(input_data or {})},
        )
        try:
            record.started_at_ms = _now_ms()
            task.on_start(context)
            record.append_event(event_name="task.started", payload={"state": context.state})
            self._append_terminal_event_if_needed(record)
        except Exception as exc:  # pragma: no cover - 当前阶段先保留最小失败兜底
            context.emit_state("failed")
            record.error = {
                "code": "task_start_failed",
                "message": str(exc),
            }
            record.completed_at_ms = _now_ms()
            record.append_event(event_name="task.failed", payload=dict(record.error))
        return record.to_snapshot()

    def query_task(self, task_id: str) -> TaskRuntimeSnapshot:
        """查询任务快照。"""

        record = self._require_record(task_id)
        self._expire_if_needed(record)
        return record.to_snapshot()

    def cancel_task(self, task_id: str) -> TaskRuntimeSnapshot:
        """取消任务。"""

        record = self._require_record(task_id)
        self._expire_if_needed(record)
        if self._is_terminal(record.context.state):
            return record.to_snapshot()
        task = self._registry.get_task(record.task_type)
        if task is None:
            raise RuntimeError(f"未注册任务类型: {record.task_type}")
        try:
            task.on_cancel(record.context)
            if record.context.state != "cancelled":
                record.context.emit_state("cancelled")
            record.completed_at_ms = _now_ms()
            record.append_event(event_name="task.cancelled", payload={"message": "任务已取消"})
        except Exception as exc:  # pragma: no cover - 当前阶段先保留最小失败兜底
            record.context.emit_state("failed")
            record.error = {
                "code": "task_cancel_failed",
                "message": str(exc),
            }
            record.completed_at_ms = _now_ms()
            record.append_event(event_name="task.failed", payload=dict(record.error))
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
        self._expire_if_needed(record)
        if self._is_terminal(record.context.state):
            record.append_event(
                event_name="task.event.ignored",
                source=source,
                payload={"event_name": event_name, "payload": dict(payload or {})},
            )
            return record.to_snapshot()
        task = self._registry.get_task(record.task_type)
        if task is None:
            raise RuntimeError(f"未注册任务类型: {record.task_type}")
        event = TaskEvent(
            name=event_name,
            payload=dict(payload or {}),
            source=source,
        )
        record.append_event(
            event_name=event_name,
            source=source,
            payload=dict(payload or {}),
        )
        try:
            task.on_event(record.context, event)
            self._append_terminal_event_if_needed(record)
        except Exception as exc:  # pragma: no cover - 当前阶段先保留最小失败兜底
            record.context.emit_state("failed")
            record.error = {
                "code": "task_event_failed",
                "message": str(exc),
            }
            record.completed_at_ms = _now_ms()
            record.append_event(event_name="task.failed", payload=dict(record.error))
        return record.to_snapshot()

    def list_tasks(self) -> list[TaskRuntimeSnapshot]:
        """列出当前全部任务快照。"""

        for record in self._records.values():
            self._expire_if_needed(record)
        return [record.to_snapshot() for record in self._records.values()]

    def export_snapshots(self) -> list[dict[str, Any]]:
        """导出全部任务快照，供宿主持久化到文件或数据库。

        返回值：
        1. 只包含 JSON 兼容字段的任务快照列表。
        """

        return [asdict(snapshot) for snapshot in self.list_tasks()]

    def save_snapshots(self, path: str | Path) -> list[dict[str, Any]]:
        """把当前任务快照保存到 JSON 文件。

        参数：
        1. `path`：目标 JSON 文件路径。

        返回值：
        1. 本次写入的快照列表，便于测试或宿主记录。

        异常情况：
        1. 文件不可写时由底层文件系统异常暴露给调用方。
        """

        snapshots = self.export_snapshots()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"tasks": snapshots}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return snapshots

    def load_snapshots(self, path: str | Path) -> list[TaskRuntimeSnapshot]:
        """从 JSON 文件加载任务快照并恢复运行时记录。

        参数：
        1. `path`：由 `save_snapshots(...)` 写出的 JSON 文件路径。

        返回值：
        1. 恢复后的任务快照列表。

        异常情况：
        1. 文件不存在、JSON 格式错误或 `tasks` 不是数组时抛出异常。
        """

        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            raise RuntimeError("任务快照文件缺少 tasks 数组")
        return self.restore_snapshots([dict(item) for item in tasks if isinstance(item, dict)])

    def restore_snapshots(self, snapshots: list[dict[str, Any] | TaskRuntimeSnapshot]) -> list[TaskRuntimeSnapshot]:
        """从快照恢复任务记录。

        主要逻辑：
        1. 恢复任务查询所需的输入、状态、上下文、结果和错误。
        2. 若任务类型仍已注册，后续事件仍可继续派发到对应 `BaseTask`。
        3. 若任务类型未注册，恢复后的记录仍可查询，但取消或事件派发会按原逻辑报错。

        参数：
        1. `snapshots`：由 `export_snapshots()` 生成的快照列表。

        返回值：
        1. 恢复后的任务快照列表。
        """

        restored: list[TaskRuntimeSnapshot] = []
        for item in snapshots:
            snapshot = self._coerce_snapshot(item)
            device_group = self._device_groups.create_context(
                device_id=snapshot.device_id,
                session_id=snapshot.session_id,
                task_id=snapshot.task_id,
            )
            context = TaskContext(
                task_id=snapshot.task_id,
                input=dict(snapshot.input_data),
                device_group=device_group,
                state=snapshot.state,
                data=dict(snapshot.data),
                result=dict(snapshot.result) if snapshot.result is not None else None,
            )
            record = _ManagedTaskRecord(
                task_id=snapshot.task_id,
                task_type=snapshot.task_type,
                session_id=snapshot.session_id,
                device_id=snapshot.device_id,
                context=context,
                error=dict(snapshot.error) if snapshot.error is not None else None,
                created_at_ms=snapshot.created_at_ms or _now_ms(),
                updated_at_ms=snapshot.updated_at_ms or _now_ms(),
                started_at_ms=snapshot.started_at_ms,
                completed_at_ms=snapshot.completed_at_ms,
                timeout_ms=snapshot.timeout_ms,
                deadline_at_ms=snapshot.deadline_at_ms,
                events=[
                    TaskRuntimeEventLog(
                        event_id=str(event.get("event_id") or f"sdk_task_evt_{uuid.uuid4().hex[:12]}"),
                        event_name=str(event.get("event_name") or "task.restored.event"),
                        state=str(event.get("state") or snapshot.state),
                        source=str(event.get("source") or "restore"),
                        payload=dict(event.get("payload") or {}),
                        ts_ms=int(event.get("ts_ms") or _now_ms()),
                    )
                    for event in snapshot.events
                ],
            )
            record.append_event(event_name="task.restored", source="restore", payload={"state": snapshot.state})
            self._records[record.task_id] = record
            restored.append(record.to_snapshot())
        return restored

    def _require_record(self, task_id: str) -> _ManagedTaskRecord:
        """读取内部任务记录。"""

        record = self._records.get(task_id)
        if record is None:
            raise RuntimeError(f"任务不存在: {task_id}")
        return record

    @staticmethod
    def _parse_timeout_ms(input_data: dict[str, Any]) -> int | None:
        """从任务输入中读取可选超时时间。"""

        raw_timeout = input_data.get("timeout_ms")
        if raw_timeout is None:
            return None
        timeout_ms = int(raw_timeout)
        if timeout_ms <= 0:
            raise RuntimeError("timeout_ms 必须大于 0")
        return timeout_ms

    @staticmethod
    def _is_terminal(state: str) -> bool:
        """判断任务状态是否为终态。"""

        return state in {"completed", "cancelled", "failed", "timeout"}

    def _expire_if_needed(self, record: _ManagedTaskRecord) -> None:
        """在查询、取消或派发事件前推进超时状态。"""

        if self._is_terminal(record.context.state):
            return
        if record.deadline_at_ms is None or _now_ms() <= record.deadline_at_ms:
            return
        record.context.emit_state("timeout")
        record.error = {
            "code": "task_timeout",
            "message": "SDK 托管任务执行超时",
            "details": {
                "task_id": record.task_id,
                "task_type": record.task_type,
                "timeout_ms": record.timeout_ms,
            },
        }
        record.completed_at_ms = _now_ms()
        record.append_event(event_name="task.timeout", payload=dict(record.error))

    def _append_terminal_event_if_needed(self, record: _ManagedTaskRecord) -> None:
        """任务进入终态时补充标准终态事件。"""

        if record.context.state == "completed":
            record.completed_at_ms = _now_ms()
            record.append_event(event_name="task.completed", payload=dict(record.context.result or {}))
            return
        if record.context.state == "cancelled":
            record.completed_at_ms = _now_ms()
            record.append_event(event_name="task.cancelled", payload={"message": "任务已取消"})
            return
        if record.context.state == "failed":
            record.completed_at_ms = _now_ms()
            record.append_event(event_name="task.failed", payload=dict(record.error or {}))

    @staticmethod
    def _coerce_snapshot(item: dict[str, Any] | TaskRuntimeSnapshot) -> TaskRuntimeSnapshot:
        """把字典或快照对象统一转换为 `TaskRuntimeSnapshot`。"""

        if isinstance(item, TaskRuntimeSnapshot):
            return item
        return TaskRuntimeSnapshot(
            task_id=str(item.get("task_id") or ""),
            task_type=str(item.get("task_type") or ""),
            session_id=str(item.get("session_id") or ""),
            device_id=str(item.get("device_id") or ""),
            state=str(item.get("state") or "created"),
            input_data=dict(item.get("input_data") or {}),
            data=dict(item.get("data") or {}),
            result=dict(item["result"]) if isinstance(item.get("result"), dict) else None,
            error=dict(item["error"]) if isinstance(item.get("error"), dict) else None,
            created_at_ms=int(item.get("created_at_ms") or 0),
            updated_at_ms=int(item.get("updated_at_ms") or 0),
            started_at_ms=int(item["started_at_ms"]) if item.get("started_at_ms") is not None else None,
            completed_at_ms=int(item["completed_at_ms"]) if item.get("completed_at_ms") is not None else None,
            timeout_ms=int(item["timeout_ms"]) if item.get("timeout_ms") is not None else None,
            deadline_at_ms=int(item["deadline_at_ms"]) if item.get("deadline_at_ms") is not None else None,
            events=[dict(event) for event in item.get("events", []) if isinstance(event, dict)],
        )


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
