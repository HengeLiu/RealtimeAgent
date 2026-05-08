"""后台任务生命周期状态机。"""

from __future__ import annotations

from infra.errors import ErrorCode, build_error


class TaskStateMachine:
    """统一任务生命周期状态机。

    主要功能：
    1. 维护允许的状态迁移规则。
    2. 避免不同任务模板各自定义不兼容状态。
    """

    _ALLOWED_TRANSITIONS = {
        "scheduled": {"running", "cancelled"},
        "running": {"waiting_external", "completed", "cancelled", "failed", "timeout"},
        "waiting_external": {"running", "completed", "cancelled", "failed", "timeout"},
        "completed": set(),
        "cancelled": set(),
        "failed": set(),
        "timeout": set(),
    }

    def ensure_transition(self, *, from_state: str, to_state: str) -> None:
        """校验状态迁移是否合法。"""

        allowed_targets = self._ALLOWED_TRANSITIONS.get(from_state)
        if allowed_targets is None or to_state not in allowed_targets:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "非法的任务状态迁移",
                details={"from_state": from_state, "to_state": to_state},
            )
