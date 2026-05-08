"""统一播放仲裁器。"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


_PRIORITY_ORDER = {
    "low": 0,
    "normal": 1,
    "high": 2,
    "critical": 3,
}


def _priority_value(priority: str) -> int:
    """把播放优先级转换为可比较数值。"""

    return _PRIORITY_ORDER.get(priority, _PRIORITY_ORDER["normal"])


@dataclass(slots=True)
class PlaybackIntent:
    """一次待播放内容的统一意图。

    主要功能：
    1. 把普通 Agent 回复、Task 通知、视觉告警和系统提示收敛成同一种模型。
    2. 为播放仲裁提供优先级、打断策略和恢复策略。
    """

    intent_id: str
    source: str
    device_id: str
    session_id: str
    stream_id: str
    priority: str = "normal"
    interrupt_policy: str = "never"
    resume_policy: str = "drop_interrupted"
    task_id: str | None = None
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        """导出可序列化摘要。"""

        return {
            "intent_id": self.intent_id,
            "source": self.source,
            "device_id": self.device_id,
            "session_id": self.session_id,
            "stream_id": self.stream_id,
            "priority": self.priority,
            "interrupt_policy": self.interrupt_policy,
            "resume_policy": self.resume_policy,
            "task_id": self.task_id,
            "created_at_ms": self.created_at_ms,
        }


@dataclass(slots=True)
class PlaybackDecision:
    """播放仲裁决策。

    主要功能：
    1. 记录某个播放意图为什么被播放、排队、抢播、丢弃或用户打断。
    2. 为运行态快照和回放断言提供解释依据。
    """

    action: str
    reason: str
    intent_id: str
    device_id: str
    active_intent_id: str | None = None
    interrupted_intent_id: str | None = None
    queue_size: int = 0
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        """导出可序列化决策记录。"""

        return {
            "action": self.action,
            "reason": self.reason,
            "intent_id": self.intent_id,
            "device_id": self.device_id,
            "active_intent_id": self.active_intent_id,
            "interrupted_intent_id": self.interrupted_intent_id,
            "queue_size": self.queue_size,
            "created_at_ms": self.created_at_ms,
        }


@dataclass(slots=True)
class PlaybackSubmitResult:
    """播放意图提交结果。"""

    decision: PlaybackDecision
    active_intent: PlaybackIntent | None = None
    interrupted_intent: PlaybackIntent | None = None


@dataclass(slots=True)
class UserInterruptResult:
    """用户打断处理结果。"""

    decision: PlaybackDecision
    interrupted_intent: PlaybackIntent | None = None
    dropped_intents: list[PlaybackIntent] = field(default_factory=list)


class PlaybackArbiter:
    """统一播放仲裁器。

    主要功能：
    1. 按设备维护当前播放意图和待播放队列。
    2. 统一处理 Agent 回复、任务通知、视觉告警和用户打断。
    3. 保留最近决策，便于运行态快照解释播放行为。
    """

    def __init__(self, *, max_recent_decisions: int = 256) -> None:
        self._active_intents: dict[str, PlaybackIntent] = {}
        self._pending_intents: dict[str, list[PlaybackIntent]] = {}
        self._intents_by_stream: dict[tuple[str, str], PlaybackIntent] = {}
        self._decisions: deque[PlaybackDecision] = deque(maxlen=max_recent_decisions)

    def submit(self, intent: PlaybackIntent) -> PlaybackSubmitResult:
        """提交一次播放意图。

        返回值：
        1. `play_now` 表示可以立即播放。
        2. `queue` 表示需要等待当前播放结束。
        3. `interrupt` 表示新意图抢占当前播放。
        """

        active = self._active_intents.get(intent.device_id)
        if active is None:
            self._active_intents[intent.device_id] = intent
            self._intents_by_stream[(intent.device_id, intent.stream_id)] = intent
            decision = self._record(
                action="play_now",
                reason="no_active_playback",
                intent=intent,
                active_intent_id=intent.intent_id,
            )
            return PlaybackSubmitResult(decision=decision, active_intent=intent)

        if self._should_interrupt(active=active, incoming=intent):
            self._active_intents[intent.device_id] = intent
            self._intents_by_stream.pop((active.device_id, active.stream_id), None)
            self._intents_by_stream[(intent.device_id, intent.stream_id)] = intent
            decision = self._record(
                action="interrupt",
                reason="incoming_policy_allows_interrupt",
                intent=intent,
                active_intent_id=intent.intent_id,
                interrupted_intent_id=active.intent_id,
                queue_size=len(self._pending_intents.get(intent.device_id, [])),
            )
            return PlaybackSubmitResult(decision=decision, active_intent=intent, interrupted_intent=active)

        pending = self._pending_intents.setdefault(intent.device_id, [])
        pending.append(intent)
        pending.sort(key=lambda item: (-_priority_value(item.priority), item.created_at_ms))
        self._intents_by_stream[(intent.device_id, intent.stream_id)] = intent
        decision = self._record(
            action="queue",
            reason="active_playback_not_interruptible",
            intent=intent,
            active_intent_id=active.intent_id,
            queue_size=len(pending),
        )
        return PlaybackSubmitResult(decision=decision, active_intent=active)

    def complete(self, *, device_id: str, stream_id: str) -> PlaybackIntent | None:
        """标记播放流完成，并返回下一条可播放意图。"""

        intent = self._intents_by_stream.pop((device_id, stream_id), None)
        active = self._active_intents.get(device_id)
        if intent is not None and active is not None and active.intent_id == intent.intent_id:
            self._active_intents.pop(device_id, None)
        pending = self._pending_intents.get(device_id, [])
        if not pending:
            self._pending_intents.pop(device_id, None)
            return None
        next_intent = pending.pop(0)
        if not pending:
            self._pending_intents.pop(device_id, None)
        self._active_intents[device_id] = next_intent
        self._record(
            action="play_now",
            reason="previous_playback_completed",
            intent=next_intent,
            active_intent_id=next_intent.intent_id,
            queue_size=len(pending),
        )
        return next_intent

    def remove(self, *, device_id: str, stream_id: str) -> PlaybackIntent | None:
        """从仲裁器中移除指定播放流。"""

        intent = self._intents_by_stream.pop((device_id, stream_id), None)
        if intent is None:
            return None
        active = self._active_intents.get(device_id)
        if active is not None and active.intent_id == intent.intent_id:
            self._active_intents.pop(device_id, None)
        pending = self._pending_intents.get(device_id, [])
        self._pending_intents[device_id] = [item for item in pending if item.intent_id != intent.intent_id]
        if not self._pending_intents[device_id]:
            self._pending_intents.pop(device_id, None)
        return intent

    def user_interrupt(
        self,
        *,
        device_id: str,
        session_id: str,
        reason: str,
        clear_queue: bool = True,
    ) -> UserInterruptResult:
        """处理用户主动打断。

        参数：
        1. `device_id/session_id`：打断来源会话。
        2. `reason`：打断原因，例如 voice_interrupt 或 button_interrupt。
        3. `clear_queue`：是否丢弃等待播放队列。
        """

        active = self._active_intents.pop(device_id, None)
        dropped = self._pending_intents.pop(device_id, []) if clear_queue else []
        if active is not None:
            self._intents_by_stream.pop((device_id, active.stream_id), None)
        for pending in dropped:
            self._intents_by_stream.pop((device_id, pending.stream_id), None)
        decision = PlaybackDecision(
            action="user_interrupt",
            reason=reason,
            intent_id=active.intent_id if active else "user_interrupt",
            device_id=device_id,
            active_intent_id=None,
            interrupted_intent_id=active.intent_id if active else None,
            queue_size=0 if clear_queue else len(self._pending_intents.get(device_id, [])),
        )
        self._decisions.append(decision)
        return UserInterruptResult(decision=decision, interrupted_intent=active, dropped_intents=dropped)

    def build_snapshot(self) -> dict[str, Any]:
        """导出仲裁器运行态快照。"""

        return {
            "active_intents": {
                device_id: intent.to_dict()
                for device_id, intent in self._active_intents.items()
            },
            "pending_intents": {
                device_id: [intent.to_dict() for intent in intents]
                for device_id, intents in self._pending_intents.items()
            },
            "recent_decisions": [decision.to_dict() for decision in self._decisions],
        }

    def _should_interrupt(self, *, active: PlaybackIntent, incoming: PlaybackIntent) -> bool:
        """判断新播放意图是否可以抢占当前播放。"""

        if incoming.interrupt_policy == "never":
            return False
        if incoming.interrupt_policy == "always":
            return True
        if incoming.interrupt_policy == "critical_only":
            return incoming.priority == "critical"
        if incoming.interrupt_policy == "higher_priority":
            return _priority_value(incoming.priority) > _priority_value(active.priority)
        return False

    def _record(
        self,
        *,
        action: str,
        reason: str,
        intent: PlaybackIntent,
        active_intent_id: str | None = None,
        interrupted_intent_id: str | None = None,
        queue_size: int = 0,
    ) -> PlaybackDecision:
        """记录一次仲裁决策。"""

        decision = PlaybackDecision(
            action=action,
            reason=reason,
            intent_id=intent.intent_id,
            device_id=intent.device_id,
            active_intent_id=active_intent_id,
            interrupted_intent_id=interrupted_intent_id,
            queue_size=queue_size,
        )
        self._decisions.append(decision)
        return decision
