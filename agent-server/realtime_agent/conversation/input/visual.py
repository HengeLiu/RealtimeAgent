from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class VisualTurnContext:
    """视觉输入 turn 上下文。

    主要功能：把当前语音 turn 关联到视觉资产生命周期，供后续视觉采样、AssetStore
    claim 或 provider realtime video append 使用。
    """

    user_id: str
    session_id: str
    stream_id: str
    reason: str
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


class VisualInputBoundary(Protocol):
    """视觉输入边界抽象。

    主要功能：按语音 turn 管理视觉采样或视觉资产接收窗口。它不判断用户意图，
    不调用视觉模型，只负责把视觉输入生命周期绑定到当前 turn。
    """

    def turn_started(
        self,
        *,
        user_id: str,
        session_id: str,
        stream_id: str,
        reason: str,
        diagnostics: Mapping[str, Any],
    ) -> None:
        """通知当前语音 turn 开始。"""

    def turn_ended(
        self,
        *,
        user_id: str,
        session_id: str,
        stream_id: str,
        reason: str,
        diagnostics: Mapping[str, Any],
    ) -> None:
        """通知当前语音 turn 结束。"""


class TurnVisualInputBoundary:
    """基于 turn 状态的视觉输入边界。

    主要功能：记录每个 session 当前活跃的视觉 turn，并可把 turn start/stop 转发给
    现有采样回调。它不判断视觉意图，也不调用视觉模型。
    """

    def __init__(
        self,
        *,
        on_started: Callable[..., None] | None = None,
        on_ended: Callable[..., None] | None = None,
    ) -> None:
        self._on_started = on_started
        self._on_ended = on_ended
        self._active_turn_by_session: dict[str, VisualTurnContext] = {}

    def turn_started(
        self,
        *,
        user_id: str,
        session_id: str,
        stream_id: str,
        reason: str,
        diagnostics: Mapping[str, Any],
    ) -> None:
        """把 turn started 转发给视觉采样回调。"""

        self._active_turn_by_session[session_id] = VisualTurnContext(
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            reason=reason,
            diagnostics=dict(diagnostics),
        )
        if self._on_started is not None:
            self._on_started(
                user_id=user_id,
                session_id=session_id,
                stream_id=stream_id,
                reason=reason,
                diagnostics=dict(diagnostics),
            )

    def turn_ended(
        self,
        *,
        user_id: str,
        session_id: str,
        stream_id: str,
        reason: str,
        diagnostics: Mapping[str, Any],
    ) -> None:
        """把 turn ended 转发给视觉采样回调。"""

        self._active_turn_by_session.pop(session_id, None)
        if self._on_ended is not None:
            self._on_ended(
                user_id=user_id,
                session_id=session_id,
                stream_id=stream_id,
                reason=reason,
                diagnostics=dict(diagnostics),
            )

    def active_turn(self, *, session_id: str) -> VisualTurnContext | None:
        """返回指定 session 当前活跃视觉 turn。"""

        return self._active_turn_by_session.get(session_id)


class CallbackVisualInputBoundary(TurnVisualInputBoundary):
    """基于回调的视觉输入边界兼容别名。

    主要功能：保留旧类名，同时继承 `TurnVisualInputBoundary` 的 turn 生命周期状态。
    """
