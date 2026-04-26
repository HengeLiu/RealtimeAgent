"""通知协调组件。"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class NotificationRequest:
    """统一通知申请对象。

    主要功能：
    1. 承载来自后台任务或对话层的通知申请。
    2. 为去重、排队和下发保留统一字段。
    """

    request_id: str
    source_module: str
    session_id: str
    device_id: str
    task_id: str | None
    priority: str
    notification_type: str
    delivery_mode: str
    allow_interrupt: bool
    allow_merge: bool
    requires_agent_context_sync: bool
    dedupe_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass(slots=True)
class NotificationSubmitResult:
    """通知提交结果。

    主要功能：
    1. 明确区分通知被接受、已直发、已排队与发生抢占。
    2. 避免调用方把“进入队列”误认为“已经播报”。
    """

    accepted: bool
    dispatched: bool
    queued: bool
    interrupted_active: bool = False


class NotificationCoordinator:
    """最小通知协调器。

    主要功能：
    1. 统一处理通知去重。
    2. 按设备维度维护活动通知与待发送队列。
    3. 在通知完成后自动放行下一条待发送通知。
    """

    _PRIORITY_ORDER = {
        "low": 0,
        "normal": 1,
        "high": 2,
        "critical": 3,
    }

    def __init__(
        self,
        *,
        dispatcher: Callable[[NotificationRequest], None],
        interrupter: Callable[[NotificationRequest], None] | None = None,
        dedupe_window_ms: int = 30000,
        max_recent_records: int = 256,
    ) -> None:
        self._dispatcher = dispatcher
        self._interrupter = interrupter
        self._dedupe_window_ms = dedupe_window_ms
        self._max_recent_records = max_recent_records
        self._lock = threading.Lock()
        self._recent_records: deque[tuple[str, int]] = deque()
        self._active_requests: dict[str, NotificationRequest] = {}
        self._pending_requests: dict[str, list[NotificationRequest]] = {}

    def submit(self, request: NotificationRequest) -> NotificationSubmitResult:
        """提交通知申请。

        返回值：
        1. `accepted=True` 表示通知通过去重检查，被系统接收。
        2. `dispatched=True` 表示通知已经立刻下发。
        3. `queued=True` 表示通知暂时排队，等待前序通知结束。
        4. `interrupted_active=True` 表示本次提交抢占了当前活动通知。
        """

        interrupted_request: NotificationRequest | None = None
        with self._lock:
            self._trim_expired_records()
            for dedupe_key, created_at_ms in self._recent_records:
                if dedupe_key == request.dedupe_key and request.created_at_ms - created_at_ms <= self._dedupe_window_ms:
                    return NotificationSubmitResult(
                        accepted=False,
                        dispatched=False,
                        queued=False,
                    )
            self._recent_records.append((request.dedupe_key, request.created_at_ms))
            while len(self._recent_records) > self._max_recent_records:
                self._recent_records.popleft()
            active_request = self._active_requests.get(request.device_id)
            if active_request is None:
                self._active_requests[request.device_id] = request
                result = NotificationSubmitResult(
                    accepted=True,
                    dispatched=True,
                    queued=False,
                )
            elif self._should_interrupt_active(active_request=active_request, incoming_request=request):
                interrupted_request = active_request
                self._active_requests[request.device_id] = request
                result = NotificationSubmitResult(
                    accepted=True,
                    dispatched=True,
                    queued=False,
                    interrupted_active=True,
                )
            else:
                pending = self._pending_requests.setdefault(request.device_id, [])
                pending.append(request)
                pending.sort(
                    key=lambda item: (
                        -self._priority_value(item.priority),
                        item.created_at_ms,
                    )
                )
                result = NotificationSubmitResult(
                    accepted=True,
                    dispatched=False,
                    queued=True,
                )

        if interrupted_request is not None and self._interrupter is not None:
            self._interrupter(interrupted_request)
        if result.dispatched:
            self._dispatcher(request)
        return result

    def complete_request(self, *, device_id: str, request_id: str) -> NotificationRequest | None:
        """标记一条通知已完成，并在需要时放行下一条通知。

        返回值：
        1. 若有新的待发送通知被放行，返回该通知。
        2. 若当前设备没有后续通知，返回 `None`。
        """

        with self._lock:
            active_request = self._active_requests.get(device_id)
            if active_request is None or active_request.request_id != request_id:
                return None
            self._active_requests.pop(device_id, None)
            pending = self._pending_requests.get(device_id, [])
            if not pending:
                self._pending_requests.pop(device_id, None)
                return None
            next_request = pending.pop(0)
            if not pending:
                self._pending_requests.pop(device_id, None)
            self._active_requests[device_id] = next_request

        self._dispatcher(next_request)
        return next_request

    def _priority_value(self, priority: str) -> int:
        """把优先级转换为可比较的整数。"""

        return self._PRIORITY_ORDER.get(priority, self._PRIORITY_ORDER["normal"])

    def _should_interrupt_active(
        self,
        *,
        active_request: NotificationRequest,
        incoming_request: NotificationRequest,
    ) -> bool:
        """判断新通知是否应抢占当前活动通知。"""

        if not incoming_request.allow_interrupt:
            return False
        return self._priority_value(incoming_request.priority) > self._priority_value(active_request.priority)

    def _trim_expired_records(self) -> None:
        """清理超出去重窗口的历史记录。"""

        current_ms = int(time.time() * 1000)
        while self._recent_records:
            _, created_at_ms = self._recent_records[0]
            if current_ms - created_at_ms <= self._dedupe_window_ms:
                break
            self._recent_records.popleft()
