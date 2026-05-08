"""SDK 托管任务运行时。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
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


class FileTaskPersistenceStore:
    """文件型任务持久化存储。

    主要功能：
    1. 把任务快照保存为单个 JSON 文件。
    2. 使用临时文件加原子替换，避免写入中断留下半截 JSON。
    3. 为单机开发、回放和轻量部署提供生产化前置形态。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, snapshots: list[dict[str, Any]]) -> None:
        """保存任务快照。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "sdk-task-store-v1",
            "saved_at_ms": _now_ms(),
            "tasks": snapshots,
        }
        tmp_path = self.path.with_name(f"{self.path.name}.tmp-{uuid.uuid4().hex[:8]}")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, self.path)

    def load(self) -> list[dict[str, Any]]:
        """读取任务快照。"""

        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            raise RuntimeError("任务持久化文件缺少 tasks 数组")
        return [dict(item) for item in tasks if isinstance(item, dict)]


class SQLiteTaskPersistenceStore:
    """SQLite 任务持久化存储。

    主要功能：
    1. 使用 Python 标准库 `sqlite3` 保存任务快照和事件日志。
    2. 为单机多进程提供 WAL、事务、事件幂等和任务租约。
    3. 保持与 `FileTaskPersistenceStore` 一致的 `save/load` 契约。
    """

    def __init__(self, path: str | Path, *, owner_id: str | None = None) -> None:
        self.path = str(path)
        self.owner_id = owner_id or f"task-owner-{uuid.uuid4().hex[:8]}"
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = sqlite3.connect(self.path)
            self._memory_connection.row_factory = sqlite3.Row
        self._initialize()

    def save(self, snapshots: list[dict[str, Any]]) -> None:
        """保存任务快照到 SQLite。"""

        now_ms = _now_ms()
        task_ids = [str(snapshot.get("task_id") or "") for snapshot in snapshots]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if task_ids:
                placeholders = ",".join("?" for _ in task_ids)
                connection.execute(f"DELETE FROM tasks WHERE task_id NOT IN ({placeholders})", task_ids)
            else:
                connection.execute("DELETE FROM tasks")
            for snapshot in snapshots:
                task_id = str(snapshot.get("task_id") or "")
                if not task_id:
                    continue
                connection.execute(
                    """
                    INSERT INTO tasks (
                        task_id, task_type, session_id, device_id, state,
                        input_json, data_json, result_json, error_json,
                        created_at_ms, updated_at_ms, started_at_ms, completed_at_ms,
                        timeout_ms, deadline_at_ms, snapshot_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        task_type=excluded.task_type,
                        session_id=excluded.session_id,
                        device_id=excluded.device_id,
                        state=excluded.state,
                        input_json=excluded.input_json,
                        data_json=excluded.data_json,
                        result_json=excluded.result_json,
                        error_json=excluded.error_json,
                        updated_at_ms=excluded.updated_at_ms,
                        started_at_ms=excluded.started_at_ms,
                        completed_at_ms=excluded.completed_at_ms,
                        timeout_ms=excluded.timeout_ms,
                        deadline_at_ms=excluded.deadline_at_ms,
                        snapshot_json=excluded.snapshot_json
                    """,
                    (
                        task_id,
                        str(snapshot.get("task_type") or ""),
                        str(snapshot.get("session_id") or ""),
                        str(snapshot.get("device_id") or ""),
                        str(snapshot.get("state") or ""),
                        self._dump_json(snapshot.get("input_data") or {}),
                        self._dump_json(snapshot.get("data") or {}),
                        self._dump_json(snapshot.get("result")),
                        self._dump_json(snapshot.get("error")),
                        int(snapshot.get("created_at_ms") or now_ms),
                        int(snapshot.get("updated_at_ms") or now_ms),
                        snapshot.get("started_at_ms"),
                        snapshot.get("completed_at_ms"),
                        snapshot.get("timeout_ms"),
                        snapshot.get("deadline_at_ms"),
                        self._dump_json(snapshot),
                    ),
                )
                for event in snapshot.get("events", []):
                    if not isinstance(event, dict):
                        continue
                    event_id = str(event.get("event_id") or f"sdk_task_evt_{uuid.uuid4().hex[:12]}")
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO task_events (
                            task_id, event_id, event_name, state, source, payload_json, ts_ms
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            event_id,
                            str(event.get("event_name") or ""),
                            str(event.get("state") or snapshot.get("state") or ""),
                            str(event.get("source") or "sdk"),
                            self._dump_json(event.get("payload") or {}),
                            int(event.get("ts_ms") or now_ms),
                        ),
                    )
            connection.commit()

    def load(self) -> list[dict[str, Any]]:
        """从 SQLite 读取任务快照。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT snapshot_json FROM tasks ORDER BY created_at_ms, task_id"
            ).fetchall()
        return [dict(json.loads(str(row["snapshot_json"]))) for row in rows]

    def acquire_lease(
        self,
        task_id: str,
        *,
        ttl_ms: int,
        owner_id: str | None = None,
        now_ms: int | None = None,
    ) -> bool:
        """尝试获取或续租任务租约。"""

        if ttl_ms <= 0:
            raise RuntimeError("ttl_ms 必须大于 0")
        current = now_ms if now_ms is not None else _now_ms()
        owner = owner_id or self.owner_id
        expires_at_ms = current + ttl_ms
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner_id, expires_at_ms FROM task_leases WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is not None and row["owner_id"] != owner and int(row["expires_at_ms"]) > current:
                connection.rollback()
                return False
            connection.execute(
                """
                INSERT INTO task_leases (task_id, owner_id, expires_at_ms, renewed_at_ms)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    owner_id=excluded.owner_id,
                    expires_at_ms=excluded.expires_at_ms,
                    renewed_at_ms=excluded.renewed_at_ms
                """,
                (task_id, owner, expires_at_ms, current),
            )
            connection.commit()
        return True

    def release_lease(self, task_id: str, *, owner_id: str | None = None) -> bool:
        """释放当前 owner 持有的任务租约。"""

        owner = owner_id or self.owner_id
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM task_leases WHERE task_id=? AND owner_id=?",
                (task_id, owner),
            )
        return cursor.rowcount > 0

    def list_leases(self) -> list[dict[str, Any]]:
        """列出任务租约，便于测试和诊断。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT task_id, owner_id, expires_at_ms, renewed_at_ms FROM task_leases ORDER BY task_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def _initialize(self) -> None:
        """初始化 SQLite schema。"""

        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            if self.path != ":memory:":
                connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at_ms INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO schema_migrations(version, applied_at_ms) VALUES (1, 0);

                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    started_at_ms INTEGER,
                    completed_at_ms INTEGER,
                    timeout_ms INTEGER,
                    deadline_at_ms INTEGER,
                    snapshot_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_events (
                    task_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    ts_ms INTEGER NOT NULL,
                    PRIMARY KEY(task_id, event_id)
                );

                CREATE TABLE IF NOT EXISTS task_leases (
                    task_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    expires_at_ms INTEGER NOT NULL,
                    renewed_at_ms INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
                CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events(task_id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        """创建 SQLite 连接。"""

        if self._memory_connection is not None:
            return self._memory_connection
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _dump_json(value: Any) -> str:
        """序列化 JSON 字段。"""

        return json.dumps(value, ensure_ascii=False, sort_keys=True)


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
        event_id: str | None = None,
    ) -> None:
        """追加任务事件日志并刷新更新时间。"""

        self.updated_at_ms = _now_ms()
        self.events.append(
            TaskRuntimeEventLog(
                event_id=event_id or f"sdk_task_evt_{uuid.uuid4().hex[:12]}",
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

    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        device_groups: Any,
        persistence_store: FileTaskPersistenceStore | None = None,
    ) -> None:
        self._registry = registry
        self._device_groups = device_groups
        self._records: dict[str, _ManagedTaskRecord] = {}
        self._persistence_store = persistence_store
        self._event_listeners: list[Any] = []
        self._scheduled_events: dict[str, threading.Timer] = {}
        self._schedule_lock = threading.Lock()

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
            scheduler=self.schedule_event,
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
        snapshot = record.to_snapshot()
        self._persist_if_configured()
        return snapshot

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
            snapshot = record.to_snapshot()
            self._persist_if_configured()
            return snapshot
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
        snapshot = record.to_snapshot()
        self._persist_if_configured()
        return snapshot

    def dispatch_event(
        self,
        *,
        task_id: str,
        event_name: str,
        payload: dict[str, Any] | None = None,
        source: str = "system",
        event_id: str | None = None,
        publish_terminal_event: bool = False,
    ) -> TaskRuntimeSnapshot:
        """向任务派发一个结构化事件。"""

        record = self._require_record(task_id)
        self._expire_if_needed(record)
        resolved_event_id = event_id or str((payload or {}).get("event_id") or (payload or {}).get("idempotency_key") or "")
        if resolved_event_id and self._has_event_id(record, resolved_event_id):
            return record.to_snapshot()
        if self._is_terminal(record.context.state):
            record.append_event(
                event_name="task.event.ignored",
                source=source,
                payload={"event_name": event_name, "payload": dict(payload or {})},
                event_id=resolved_event_id or None,
            )
            snapshot = record.to_snapshot()
            self._persist_if_configured()
            return snapshot
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
            event_id=resolved_event_id or None,
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
        snapshot = record.to_snapshot()
        self._persist_if_configured()
        if publish_terminal_event and self._is_terminal(record.context.state):
            self._publish_terminal_event(record)
        return snapshot

    def schedule_event(
        self,
        *,
        task_id: str,
        delay_ms: int,
        event_name: str,
        payload: dict[str, Any] | None = None,
        source: str = "scheduler",
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """安排一次性延迟任务事件。

        功能：
        1. 为 SDK 自定义 Task 提供公开、通用的定时调度接口。
        2. 到点后自动调用 `dispatch_event(...)` 推进任务。
        3. 如果任务已经终止，到点事件会按既有终态保护被忽略。

        参数：
        1. `task_id`：目标任务编号。
        2. `delay_ms`：延迟毫秒数，必须大于 0。
        3. `event_name`：到点后派发的事件名。
        4. `payload`：事件载荷。
        5. `source`：事件来源。
        6. `event_id`：可选幂等编号。

        返回值：
        1. 调度记录字典。

        异常情况：
        1. 任务不存在、延迟时间非法或事件名为空时抛出 `RuntimeError`。
        """

        self._require_record(task_id)
        if delay_ms <= 0:
            raise RuntimeError("delay_ms 必须大于 0")
        normalized_event_name = event_name.strip()
        if not normalized_event_name:
            raise RuntimeError("event_name 不能为空")
        schedule_id = f"sched_{uuid.uuid4().hex[:12]}"
        due_at_ms = _now_ms() + delay_ms

        def _fire() -> None:
            with self._schedule_lock:
                self._scheduled_events.pop(schedule_id, None)
            self.dispatch_event(
                task_id=task_id,
                event_name=normalized_event_name,
                payload=dict(payload or {}),
                source=source,
                event_id=event_id or schedule_id,
                publish_terminal_event=True,
            )

        timer = threading.Timer(delay_ms / 1000, _fire)
        timer.daemon = True
        with self._schedule_lock:
            self._scheduled_events[schedule_id] = timer
        timer.start()
        record = self._require_record(task_id)
        record.append_event(
            event_name="task.event.scheduled",
            source="scheduler",
            payload={
                "schedule_id": schedule_id,
                "event_name": normalized_event_name,
                "delay_ms": delay_ms,
                "due_at_ms": due_at_ms,
                "source": source,
            },
        )
        self._persist_if_configured()
        return {
            "schedule_id": schedule_id,
            "task_id": task_id,
            "event_name": normalized_event_name,
            "delay_ms": delay_ms,
            "due_at_ms": due_at_ms,
        }

    def cancel_scheduled_event(self, schedule_id: str) -> bool:
        """取消尚未触发的一次性调度事件。"""

        with self._schedule_lock:
            timer = self._scheduled_events.pop(schedule_id, None)
        if timer is None:
            return False
        timer.cancel()
        return True

    def list_scheduled_events(self) -> list[dict[str, Any]]:
        """列出当前仍在等待触发的调度事件。"""

        with self._schedule_lock:
            return [
                {
                    "schedule_id": schedule_id,
                    "alive": timer.is_alive(),
                }
                for schedule_id, timer in sorted(self._scheduled_events.items())
            ]

    def subscribe_events(self, listener) -> None:
        """订阅 SDK 任务运行时内部发布的事件。

        当前主要用于调度器到点后触发的终态事件回流。普通外部派发路径仍由
        `HybridTaskGateway` 负责发布，避免重复事件。
        """

        self._event_listeners.append(listener)

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

    def enable_persistence(
        self,
        path: str | Path,
        *,
        restore: bool = False,
    ) -> list[TaskRuntimeSnapshot]:
        """启用文件持久化。

        参数：
        1. `path`：任务持久化文件路径。
        2. `restore`：是否立即从文件恢复已有任务。

        返回值：
        1. `restore=True` 时返回恢复任务列表，否则返回空列表。
        """

        self._persistence_store = FileTaskPersistenceStore(path)
        if not restore:
            self._persist_if_configured()
            return []
        restored = self.restore_snapshots(self._persistence_store.load())
        self._persist_if_configured()
        return restored

    def enable_sqlite_persistence(
        self,
        path: str | Path,
        *,
        restore: bool = False,
        owner_id: str | None = None,
    ) -> list[TaskRuntimeSnapshot]:
        """启用 SQLite 持久化。

        参数：
        1. `path`：SQLite 文件路径，或 `:memory:`。
        2. `restore`：是否立即从 SQLite 恢复已有任务。
        3. `owner_id`：当前进程或 worker 的租约 owner 编号。

        返回值：
        1. `restore=True` 时返回恢复任务列表，否则返回空列表。
        """

        self._persistence_store = SQLiteTaskPersistenceStore(path, owner_id=owner_id)
        if not restore:
            self._persist_if_configured()
            return []
        restored = self.restore_snapshots(self._persistence_store.load())
        self._persist_if_configured()
        return restored

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
                scheduler=self.schedule_event,
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
        self._persist_if_configured()
        return restored

    def prune_tasks(self, *, retain_terminal_ms: int, now_ms: int | None = None) -> list[str]:
        """清理已结束且超过保留期的任务。

        参数：
        1. `retain_terminal_ms`：终态任务保留时长。
        2. `now_ms`：测试注入的当前时间。

        返回值：
        1. 被清理的任务编号列表。
        """

        if retain_terminal_ms < 0:
            raise RuntimeError("retain_terminal_ms 不能小于 0")
        current = now_ms if now_ms is not None else _now_ms()
        removed: list[str] = []
        for task_id, record in list(self._records.items()):
            if not self._is_terminal(record.context.state):
                continue
            completed_at_ms = record.completed_at_ms or record.updated_at_ms
            if current - completed_at_ms < retain_terminal_ms:
                continue
            self._records.pop(task_id, None)
            removed.append(task_id)
        if removed:
            self._persist_if_configured()
        return removed

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

    def _publish_terminal_event(self, record: _ManagedTaskRecord) -> None:
        """把调度器触发的终态任务事件发布给外部监听器。"""

        if record.context.state == "completed":
            self._emit_task_event(record=record, event_name="task.completed", payload=dict(record.context.result or {}))
            return
        if record.context.state == "cancelled":
            self._emit_task_event(record=record, event_name="task.cancelled", payload={"message": "任务已取消"})
            return
        if record.context.state == "failed":
            self._emit_task_event(record=record, event_name="task.failed", payload=dict(record.error or {}))
            return
        if record.context.state == "timeout":
            self._emit_task_event(record=record, event_name="task.timeout", payload=dict(record.error or {}))

    def _emit_task_event(self, *, record: _ManagedTaskRecord, event_name: str, payload: dict[str, Any]) -> None:
        """按 Task 声明的终态策略发布 backend-task-core 事件。"""

        from backend_task_core import TaskEvent as BackendTaskEvent

        policy = self.get_terminal_event_policy(record.task_type)
        event = BackendTaskEvent(
            event_id=f"sdk_evt_{record.task_id}_{event_name}_{uuid.uuid4().hex[:8]}",
            event_name=event_name,
            task_id=record.task_id,
            task_type=record.task_type,
            session_id=record.session_id,
            device_id=record.device_id,
            state=record.context.state,
            priority=policy["priority"],
            requires_agent_decision=policy["requires_agent_decision"],
            allow_direct_notify=policy["allow_direct_notify"],
            ts=_now_ms(),
            payload=payload,
        )
        for listener in list(self._event_listeners):
            listener(event)

    def get_terminal_event_policy(self, task_type: str) -> dict[str, Any]:
        """读取任务声明的终态事件策略。"""

        task = self._registry.get_task(task_type)
        return {
            "requires_agent_decision": bool(getattr(task, "terminal_event_requires_agent_decision", False)),
            "allow_direct_notify": bool(getattr(task, "terminal_event_allow_direct_notify", True)),
            "priority": str(getattr(task, "terminal_event_priority", "normal") or "normal"),
        }

    @staticmethod
    def _has_event_id(record: _ManagedTaskRecord, event_id: str) -> bool:
        """判断事件编号是否已处理过。"""

        return any(event.event_id == event_id for event in record.events)

    def _persist_if_configured(self) -> None:
        """如果配置了持久化存储，则立即保存当前快照。"""

        if self._persistence_store is None:
            return
        self._persistence_store.save(self.export_snapshots())

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

    def schedule_event(
        self,
        *,
        task_id: str,
        delay_ms: int,
        event_name: str,
        payload: dict[str, Any] | None = None,
        source: str = "scheduler",
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """通过旧任务网关安排 SDK 自定义任务延迟事件。"""

        scheduler = getattr(self._task_gateway, "schedule_event", None)
        if scheduler is None:
            raise RuntimeError("当前任务网关不支持通用定时调度")
        return scheduler(
            task_id=task_id,
            delay_ms=delay_ms,
            event_name=event_name,
            payload=dict(payload or {}),
            source=source,
            event_id=event_id,
        )

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
