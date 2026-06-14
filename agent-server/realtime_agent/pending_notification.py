"""会话关闭后的待通知 late result 存储。

主要功能：当 late result 到达时会话已关闭，把结果按用户维度落盘；下次用户唤醒或
会话打开时消费未过期条目，作为上下文注入模型首轮。

设计依据：docs/internal/ToolRun统一异步工具调用设计.md 第 7.3 / 第 8 节。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from realtime_agent.protocol import new_id


@dataclass
class PendingNotification:
    """一条待通知 late result。"""

    notification_id: str
    user_id: str
    session_id: str
    run_id: str
    tool_name: str
    text: str
    source: str = "tool_run"
    created_at: float = field(default_factory=time.time)
    ttl_seconds: float = 0.0
    consumed: bool = False

    @classmethod
    def create(
        cls,
        *,
        user_id: str,
        session_id: str,
        run_id: str,
        tool_name: str,
        text: str,
        source: str = "tool_run",
        ttl_seconds: float = 0.0,
    ) -> "PendingNotification":
        return cls(
            notification_id=new_id("pending_notify"),
            user_id=str(user_id or ""),
            session_id=str(session_id or ""),
            run_id=str(run_id or ""),
            tool_name=str(tool_name or ""),
            text=str(text or ""),
            source=str(source or "tool_run"),
            created_at=time.time(),
            ttl_seconds=float(ttl_seconds or 0.0),
        )

    def is_expired(self, now: float) -> bool:
        """判断是否超过 TTL。"""

        return self.ttl_seconds > 0 and now > self.created_at + self.ttl_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "tool_name": self.tool_name,
            "text": self.text,
            "source": self.source,
            "created_at": self.created_at,
            "ttl_seconds": self.ttl_seconds,
            "consumed": self.consumed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PendingNotification":
        return cls(
            notification_id=str(data.get("notification_id") or new_id("pending_notify")),
            user_id=str(data.get("user_id") or ""),
            session_id=str(data.get("session_id") or ""),
            run_id=str(data.get("run_id") or ""),
            tool_name=str(data.get("tool_name") or ""),
            text=str(data.get("text") or ""),
            source=str(data.get("source") or "tool_run"),
            created_at=float(data.get("created_at") or time.time()),
            ttl_seconds=float(data.get("ttl_seconds") or 0.0),
            consumed=bool(data.get("consumed")),
        )


class PendingNotificationStore:
    """进程内待通知存储。"""

    def __init__(self) -> None:
        self._items: dict[str, PendingNotification] = {}
        self._lock = threading.Lock()

    def add(self, notification: PendingNotification) -> None:
        """写入一条待通知。"""

        with self._lock:
            self._items[notification.notification_id] = notification
            self._persist(notification)

    def consume_unexpired(self, user_id: str, *, now: float | None = None) -> list[PendingNotification]:
        """取出某用户未过期未消费的待通知并标记已消费。

        主要逻辑：未过期条目返回并标记 consumed；过期条目直接标记 consumed 丢弃。
        参数：`user_id` 为目标用户；`now` 为当前时间。
        返回值：未过期待通知列表（按创建时间升序）。
        异常情况：无。
        """

        current = time.time() if now is None else now
        delivered: list[PendingNotification] = []
        with self._lock:
            for notification in sorted(self._items.values(), key=lambda item: item.created_at):
                if notification.user_id != user_id or notification.consumed:
                    continue
                notification.consumed = True
                self._persist(notification)
                if not notification.is_expired(current):
                    delivered.append(notification)
        return delivered

    def list_pending(self, user_id: str) -> list[PendingNotification]:
        """返回某用户未消费的待通知（用于调试/测试）。"""

        with self._lock:
            return [item for item in self._items.values() if item.user_id == user_id and not item.consumed]

    def _persist(self, notification: PendingNotification) -> None:
        """持久化钩子，内存实现为空。"""


class JsonlPendingNotificationStore(PendingNotificationStore):
    """JSONL 持久化待通知存储。"""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            data = record.get("notification") if isinstance(record, dict) else None
            if isinstance(data, dict):
                notification = PendingNotification.from_dict(data)
                self._items[notification.notification_id] = notification

    def _persist(self, notification: PendingNotification) -> None:
        record = {"record_type": "pending_notification.snapshot", "notification": notification.to_dict()}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
