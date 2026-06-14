"""FollowUpRouter 决策与排队测试（Phase 4）。

测试目标：验证 late result 在“活跃空闲 / 活跃忙 / 会话关闭 / 过期”四种场景的裁决，
以及 flush 的幂等与 ToolRun 状态推进。
"""

from __future__ import annotations

import time

from realtime_agent.conversation.follow_up import (
    FollowUpCompletion,
    FollowUpRouter,
    PendingFollowUpQueue,
)
from realtime_agent.tool_run import ToolRun, ToolRunStore
from realtime_agent.tools import ToolError, ToolResult
from realtime_agent.errors import ErrorCode


class _FakeInjector:
    """可配置的 fake 注入通道。"""

    channel_name = "fake"

    def __init__(self, *, active: bool = True, idle: bool = True, inject_ok: bool = True) -> None:
        self.active = active
        self.idle = idle
        self.inject_ok = inject_ok
        self.injected: list[FollowUpCompletion] = []

    def is_session_active(self, user_id: str, session_id: str) -> bool:
        return self.active

    def is_turn_idle(self, user_id: str, session_id: str) -> bool:
        return self.idle

    def inject(self, completion: FollowUpCompletion) -> bool:
        if self.inject_ok:
            self.injected.append(completion)
        return self.inject_ok


def _store_with_run(*, follow_up_deadline_at=None) -> tuple[ToolRunStore, ToolRun]:
    """构造一个已处于 completed_late 的 Tool Run。"""

    store = ToolRunStore()
    run = ToolRun.create(tool_name="query_route_plan", user_id="u1", session_id="s1", result_policy="background")
    run.follow_up_deadline_at = follow_up_deadline_at
    store.put(run)
    store.try_transition(run.run_id, from_states={"running"}, to_state="reported_running")
    store.try_transition(run.run_id, from_states={"reported_running"}, to_state="completed_late", result={"ok": True})
    return store, run


def _completion(run: ToolRun, *, ok: bool = True, deadline=None) -> FollowUpCompletion:
    return FollowUpCompletion(
        run_id=run.run_id,
        user_id=run.user_id,
        session_id=run.session_id,
        tool_name=run.tool_name,
        text="（系统通知）路线已查到：步行约 800 米。",
        ok=ok,
        follow_up_deadline_at=deadline if deadline is not None else run.follow_up_deadline_at,
    )


def test_active_idle_injects_and_marks_followed_up() -> None:
    """测试目标：活跃且空闲时注入并把运行推进到 followed_up。"""

    store, run = _store_with_run()
    injector = _FakeInjector(active=True, idle=True)
    router = FollowUpRouter(store=store, injector=injector)
    decision = router.submit(_completion(run))
    assert decision == "followed_up"
    assert len(injector.injected) == 1
    saved = store.get(run.run_id)
    assert saved.state == "followed_up"
    assert saved.follow_up["channel"] == "fake"


def test_active_busy_enqueues_then_flush_injects() -> None:
    """测试目标：活跃但忙时进入 pending queue，flush 后注入。"""

    store, run = _store_with_run()
    injector = _FakeInjector(active=True, idle=False)
    queue = PendingFollowUpQueue()
    router = FollowUpRouter(store=store, injector=injector, pending_queue=queue)
    decision = router.submit(_completion(run))
    assert decision == "queued"
    assert queue.pending_count("u1") == 1
    assert store.get(run.run_id).state == "completed_late"

    # turn 结束变空闲后 flush。
    injector.idle = True
    router.flush("u1")
    assert len(injector.injected) == 1
    assert store.get(run.run_id).state == "followed_up"
    assert queue.pending_count("u1") == 0


def test_session_closed_routes_to_pending_notification() -> None:
    """测试目标：会话不活跃时走待通知路径，并触发 on_session_closed。"""

    store, run = _store_with_run()
    injector = _FakeInjector(active=False)
    closed: list = []
    router = FollowUpRouter(store=store, injector=injector, on_session_closed=closed.append)
    decision = router.submit(_completion(run))
    assert decision == "pending_notification"
    assert len(closed) == 1
    saved = store.get(run.run_id)
    assert saved.state == "followed_up"
    assert saved.follow_up["channel"] == "wake_context"


def test_expired_completion_marked_expired() -> None:
    """测试目标：超过 follow-up TTL 的 late result 置 expired，不注入。"""

    store, run = _store_with_run(follow_up_deadline_at=time.time() - 1)
    injector = _FakeInjector(active=True, idle=True)
    router = FollowUpRouter(store=store, injector=injector)
    decision = router.submit(_completion(run))
    assert decision == "expired"
    assert injector.injected == []
    assert store.get(run.run_id).state == "expired"


def test_submit_is_idempotent_for_already_handled_run() -> None:
    """测试目标：对已 followed_up 的运行再次 submit 不会重复注入。"""

    store, run = _store_with_run()
    injector = _FakeInjector(active=True, idle=True)
    router = FollowUpRouter(store=store, injector=injector)
    assert router.submit(_completion(run)) == "followed_up"
    # 第二次（例如 flush 与新完成竞态）应被状态守卫跳过。
    assert router.submit(_completion(run)) == "skipped"
    assert len(injector.injected) == 1


def test_inject_failure_falls_back_to_pending_notification() -> None:
    """测试目标：注入返回 False（竞态关闭）时兜底走待通知。"""

    store, run = _store_with_run()
    injector = _FakeInjector(active=True, idle=True, inject_ok=False)
    closed: list = []
    router = FollowUpRouter(store=store, injector=injector, on_session_closed=closed.append)
    decision = router.submit(_completion(run))
    assert decision == "pending_notification"
    assert len(closed) == 1
    assert store.get(run.run_id).state == "followed_up"


def test_on_tool_run_complete_builds_completion_text() -> None:
    """测试目标：on_tool_run_complete 从 ToolResult 构造回流文本并注入。"""

    store, run = _store_with_run()
    injector = _FakeInjector(active=True, idle=True)
    router = FollowUpRouter(store=store, injector=injector)
    result = ToolResult.success(data={"route": "ready"}, message="步行约 800 米，预计 12 分钟。")
    router.on_tool_run_complete(run, result)
    assert len(injector.injected) == 1
    assert "步行约 800 米" in injector.injected[0].text


def test_failed_tool_run_text_states_failure() -> None:
    """测试目标：失败 late result 文本如实表达未成功。"""

    store, run = _store_with_run()
    injector = _FakeInjector(active=True, idle=True)
    router = FollowUpRouter(store=store, injector=injector)
    result = ToolResult.failed(ToolError("外部服务不可用", code=ErrorCode.UNKNOWN))
    router.on_tool_run_complete(run, result)
    assert len(injector.injected) == 1
    assert "没有成功" in injector.injected[0].text
