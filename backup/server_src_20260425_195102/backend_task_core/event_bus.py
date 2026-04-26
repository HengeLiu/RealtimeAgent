"""后台任务事件总线。"""

from __future__ import annotations

import threading
from collections.abc import Callable

from backend_task_core.models import TaskEvent


class TaskEventBus:
    """任务事件发布订阅总线。

    主要功能：
    1. 允许运行时发布结构化任务事件。
    2. 允许外部模块订阅任务事件，例如通知层或联调测试。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._listeners: list[Callable[[TaskEvent], None]] = []

    def subscribe(self, listener: Callable[[TaskEvent], None]) -> None:
        """注册事件监听器。"""

        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def publish(self, event: TaskEvent) -> None:
        """发布一条任务事件。"""

        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            listener(event)
