"""单轮交互流式事件与协调器。"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Any

from agent_core.context.models import generate_id, now_ms


@dataclass(slots=True)
class TurnEvent:
    """单轮交互中的标准事件。

    主要功能：
        用统一结构记录一轮输入从开始、模型处理、工具调用到输出完成的关键状态。

    主要属性：
        event_id: 事件唯一编号。
        event_type: 事件类型，例如 `turn.started`、`text.final`。
        session_id: 会话编号。
        turn_id: 轮次编号。
        generation_id: 本轮输出代际编号，未来用于丢弃旧输出。
        stream_id: 输入或输出流编号。
        causation_id: 触发本事件的上游事件编号。
        payload: 事件补充信息。
        created_at_ms: 事件创建时间。
    """

    event_type: str
    session_id: str
    turn_id: str
    generation_id: str
    stream_id: str = ""
    causation_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: generate_id("event"))
    created_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        """转换为可写入日志或 `AgentTurnResult.meta` 的字典。"""

        return asdict(self)


class TurnCoordinator:
    """管理当前活跃 turn 与 generation 的轻量协调器。

    主要功能：
        1. 为每轮交互分配稳定的 `generation_id`。
        2. 记录本轮关键事件，便于日志聚合和后续流式节点改造。
        3. 提供 `is_current(...)`，为未来输出侧 generation gating 预留判断入口。

    主要方法：
        `start_turn(...)` 开始新轮次；`emit(...)` 记录普通事件；
        `finish_turn(...)` / `fail_turn(...)` 关闭轮次；`snapshot(...)` 返回事件快照。

    主要属性：
        `_active_generations` 保存每个会话当前 generation；
        `_events_by_turn` 保存每个 turn 最近事件。
    """

    def __init__(self, *, max_events_per_turn: int = 64) -> None:
        self._max_events_per_turn = max_events_per_turn
        self._lock = threading.RLock()
        self._active_generations: dict[tuple[str, str], str] = {}
        self._events_by_turn: dict[str, list[TurnEvent]] = {}

    def start_turn(
        self,
        *,
        session_id: str,
        device_id: str,
        turn_id: str,
        stream_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> TurnEvent:
        """开始一轮交互并分配新的 generation。

        参数：
            session_id: 会话编号。
            device_id: 设备编号。
            turn_id: 轮次编号。
            stream_id: 输入流编号，可为空。
            payload: 起始事件补充信息。

        返回值：
            `turn.started` 事件。

        异常情况：
            本方法只操作内存状态，不主动抛出业务异常。
        """

        generation_id = generate_id("gen")
        event = TurnEvent(
            event_type="turn.started",
            session_id=session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            stream_id=stream_id,
            payload=payload or {},
        )
        with self._lock:
            self._active_generations[(session_id, device_id)] = generation_id
            self._events_by_turn[turn_id] = [event]
        return event

    def emit(
        self,
        *,
        event_type: str,
        session_id: str,
        device_id: str,
        turn_id: str,
        generation_id: str,
        stream_id: str = "",
        causation_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> TurnEvent:
        """记录一条普通事件。"""

        event = TurnEvent(
            event_type=event_type,
            session_id=session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            stream_id=stream_id,
            causation_id=causation_id,
            payload=payload or {},
        )
        with self._lock:
            events = self._events_by_turn.setdefault(turn_id, [])
            events.append(event)
            if len(events) > self._max_events_per_turn:
                del events[0 : len(events) - self._max_events_per_turn]
        return event

    def finish_turn(
        self,
        *,
        session_id: str,
        device_id: str,
        turn_id: str,
        generation_id: str,
        stream_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> TurnEvent:
        """记录 turn 完成事件并清理活跃 generation。"""

        event = self.emit(
            event_type="turn.finished",
            session_id=session_id,
            device_id=device_id,
            turn_id=turn_id,
            generation_id=generation_id,
            stream_id=stream_id,
            payload=payload,
        )
        with self._lock:
            if self._active_generations.get((session_id, device_id)) == generation_id:
                self._active_generations.pop((session_id, device_id), None)
        return event

    def fail_turn(
        self,
        *,
        session_id: str,
        device_id: str,
        turn_id: str,
        generation_id: str,
        stream_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> TurnEvent:
        """记录 turn 失败事件并清理活跃 generation。"""

        event = self.emit(
            event_type="turn.failed",
            session_id=session_id,
            device_id=device_id,
            turn_id=turn_id,
            generation_id=generation_id,
            stream_id=stream_id,
            payload=payload,
        )
        with self._lock:
            if self._active_generations.get((session_id, device_id)) == generation_id:
                self._active_generations.pop((session_id, device_id), None)
        return event

    def is_current(self, *, session_id: str, device_id: str, generation_id: str) -> bool:
        """判断给定 generation 是否仍是当前活跃输出。"""

        with self._lock:
            return self._active_generations.get((session_id, device_id)) == generation_id

    def snapshot(self, *, turn_id: str) -> list[dict[str, Any]]:
        """返回指定 turn 的事件快照。"""

        with self._lock:
            return [event.to_dict() for event in self._events_by_turn.get(turn_id, [])]
