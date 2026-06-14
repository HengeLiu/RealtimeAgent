"""direct 通道与后台超时强制测试（Phase B）。

测试目标：验证 late_result_notify=direct 经 OutputService 直通播报、会话关闭走待通知，
以及后台总超时被强制触发并归一为 failed(timeout)。
"""

from __future__ import annotations

import asyncio
import time

from realtime_agent.conversation.follow_up import FollowUpCompletion, FollowUpRouter
from realtime_agent.tool_run import ToolRun, ToolRunRunner, ToolRunStore
from realtime_agent.tools import (
    BaseTool,
    ToolContext,
    ToolDeviceFacade,
    ToolExecutor,
    ToolResult,
    ToolSpec,
)


def _context(user_id: str = "u1", session_id: str = "s1") -> ToolContext:
    return ToolContext(user_id=user_id, session_id=session_id, devices=ToolDeviceFacade(context=None))


class _RecordingOutput:
    """记录 notify_tool_run 调用的 fake OutputService。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def notify_tool_run(self, **kwargs) -> None:
        self.calls.append(kwargs)


class _IdleInjector:
    channel_name = "fake"

    def __init__(self, active: bool = True) -> None:
        self.active = active
        self.injected: list = []

    def is_session_active(self, user_id, session_id) -> bool:
        return self.active

    def is_turn_idle(self, user_id, session_id) -> bool:
        return True

    def inject(self, completion) -> bool:
        self.injected.append(completion)
        return True


def _completed_late_run(store: ToolRunStore, *, notify_policy: str) -> ToolRun:
    run = ToolRun.create(
        tool_name="start_timer",
        user_id="u1",
        session_id="s1",
        result_policy="background",
        metadata={"notify_policy": notify_policy},
    )
    store.put(run)
    store.try_transition(run.run_id, from_states={"running"}, to_state="reported_running")
    store.try_transition(run.run_id, from_states={"reported_running"}, to_state="completed_late", result={"ok": True})
    return run


def _completion(run: ToolRun, *, notify_policy: str) -> FollowUpCompletion:
    return FollowUpCompletion(
        run_id=run.run_id,
        user_id=run.user_id,
        session_id=run.session_id,
        tool_name=run.tool_name,
        text="时间到了。",
        notify_policy=notify_policy,
    )


def test_direct_notify_routes_to_output_service() -> None:
    """测试目标：direct 通道活跃会话时经 OutputService 直通播报，不注入模型。"""

    store = ToolRunStore()
    output = _RecordingOutput()
    injector = _IdleInjector(active=True)
    router = FollowUpRouter(store=store, injector=injector, output_service=output)
    run = _completed_late_run(store, notify_policy="direct")
    decision = router.submit(_completion(run, notify_policy="direct"))
    assert decision == "direct_notified"
    assert len(output.calls) == 1
    assert output.calls[0]["text"] == "时间到了。"
    assert injector.injected == []
    assert store.get(run.run_id).state == "followed_up"
    assert store.get(run.run_id).follow_up["channel"] == "direct_notify"


def test_direct_notify_closed_session_goes_pending() -> None:
    """测试目标：direct 通道会话关闭时仍走待通知，不直接播报。"""

    store = ToolRunStore()
    output = _RecordingOutput()
    closed: list = []
    router = FollowUpRouter(store=store, injector=_IdleInjector(active=False), output_service=output, on_session_closed=closed.append)
    run = _completed_late_run(store, notify_policy="direct")
    decision = router.submit(_completion(run, notify_policy="direct"))
    assert decision == "pending_notification"
    assert output.calls == []
    assert len(closed) == 1


def test_model_policy_still_injects() -> None:
    """测试目标：model 通道维持注入模型行为。"""

    store = ToolRunStore()
    injector = _IdleInjector(active=True)
    router = FollowUpRouter(store=store, injector=injector, output_service=_RecordingOutput())
    run = _completed_late_run(store, notify_policy="model")
    decision = router.submit(_completion(run, notify_policy="model"))
    assert decision == "followed_up"
    assert len(injector.injected) == 1


def test_background_timeout_enforced() -> None:
    """测试目标：后台运行超过总超时时被强制取消并归一为 failed(timeout)。

    测试方法：工具 sleep 远超 background_timeout，等待窗口超窗返回 running 后，
    background 超时到点触发 TimeoutError。
    预期结果：状态 failed，metadata.error.reason=timeout。
    """

    class _OverBudgetTool(BaseTool):
        spec = ToolSpec(
            name="over_budget_tool",
            description="超过后台预算的工具。",
            late_result_policy="background",
            background_timeout_seconds=0.3,
        )

        async def run(self, context, input_data: dict) -> ToolResult:
            await asyncio.sleep(10)
            return ToolResult.success()

    executor = ToolExecutor(max_wait_timeout_seconds=0.1, store=ToolRunStore(), runner=ToolRunRunner())
    result = asyncio.run(executor.execute(_OverBudgetTool(), _context(), {}))
    assert result.status == "running"
    run_id = result.data["tool_run_id"]

    deadline = time.time() + 3.0
    while time.time() < deadline and executor.store.get(run_id).state not in {"failed", "completed_late"}:
        time.sleep(0.02)
    run = executor.store.get(run_id)
    assert run.state == "failed"
    assert run.metadata.get("error", {}).get("reason") == "timeout"


def test_per_input_background_budget_override() -> None:
    """测试目标：工具可按入参覆写后台预算（background_timeout_seconds_for）。"""

    class _TimerLikeTool(BaseTool):
        spec = ToolSpec(
            name="timer_like_tool",
            description="按入参定预算的工具。",
            late_result_policy="background",
        )

        def background_timeout_seconds_for(self, input_data: dict) -> float:
            return float(input_data.get("seconds") or 1) + 5.0

        async def run(self, context, input_data: dict) -> ToolResult:
            await asyncio.sleep(0.1)
            return ToolResult.success()

    executor = ToolExecutor(max_wait_timeout_seconds=0.05, store=ToolRunStore(), runner=ToolRunRunner())
    result = asyncio.run(executor.execute(_TimerLikeTool(), _context(), {"seconds": 20}))
    run_id = result.data["tool_run_id"]
    run = executor.store.get(run_id)
    # 预算 = 20 + 5 = 25 秒，deadline 远大于现在。
    assert run.deadline_at is not None and run.deadline_at - time.time() > 20
