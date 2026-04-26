"""后台任务上下文存储。"""

from __future__ import annotations

import copy
import threading

from backend_task_core.models import TaskRuntime


class TaskContextStore:
    """任务实例线程安全存储。

    主要功能：
    1. 维护 `task_id -> TaskRuntime` 索引。
    2. 为创建、查询、更新和取消任务提供统一存储面。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskRuntime] = {}

    def save(self, runtime: TaskRuntime) -> TaskRuntime:
        """保存或覆盖任务实例。"""

        with self._lock:
            self._tasks[runtime.task_id] = copy.deepcopy(runtime)
            return copy.deepcopy(runtime)

    def get(self, task_id: str) -> TaskRuntime | None:
        """读取任务实例。"""

        with self._lock:
            runtime = self._tasks.get(task_id)
            return copy.deepcopy(runtime) if runtime is not None else None

    def update(self, runtime: TaskRuntime) -> TaskRuntime:
        """更新任务实例。"""

        return self.save(runtime)
