"""Omni 链路 late result 注入测试（Phase 5）。

测试目标：验证 OmniRealtimeAgentCore.inject_followup_result 把 late result 写入历史
并经 provider instructions 注入；is_turn_idle 随活跃 response 变化；FollowUpRouter 经
OmniFollowUpInjector 在空闲会话注入、忙时排队、turn 完成后 flush。
"""

from __future__ import annotations

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.conversation.follow_up import FollowUpCompletion, FollowUpRouter, OmniFollowUpInjector


def _omni_app(tmp_path):
    """构造 omni 模式 app（mock provider），返回 (app, core)。"""

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="omni", omni_provider="mock"))
    core = getattr(app.agent_core, "core", app.agent_core)
    return app, core


def test_omni_inject_followup_writes_message_and_instructions(tmp_path) -> None:
    """测试目标：注入 late result 写入 user 消息并向 provider 提交 instructions。"""

    app, core = _omni_app(tmp_path)
    user_id = "user-omni-late"
    session_id = "sess-omni-late"
    core.open(user_id, session_id)

    ok = core.inject_followup_result(
        user_id=user_id,
        session_id=session_id,
        text="（系统通知）刚才发起的路线规划已经有结果了：步行约 800 米。请用一句话告诉用户。",
        run_id="tool_run_omni",
    )
    assert ok is True

    _stored_session, provider = core._sessions[user_id]
    assert getattr(provider, "followup_instructions", [])
    assert "步行约 800 米" in provider.followup_instructions[0]

    session_dir = tmp_path / "runs" / user_id / session_id
    message_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
    assert "tool_result.late.done" in message_text
    assert "步行约 800 米" in message_text


def test_omni_inject_returns_false_when_session_closed(tmp_path) -> None:
    """测试目标：会话未打开时注入返回 False。"""

    app, core = _omni_app(tmp_path)
    ok = core.inject_followup_result(user_id="u-x", session_id="s-x", text="late", run_id="r")
    assert ok is False


def test_omni_is_turn_idle_tracks_active_response(tmp_path) -> None:
    """测试目标：is_turn_idle 随活跃 response 变化。"""

    app, core = _omni_app(tmp_path)
    user_id = "user-omni-idle"
    session_id = "sess-omni-idle"
    core.open(user_id, session_id)
    assert core.is_turn_idle(user_id, session_id) is True
    core._active_response_sessions.add(session_id)
    assert core.is_turn_idle(user_id, session_id) is False
    core._active_response_sessions.discard(session_id)
    assert core.is_turn_idle(user_id, session_id) is True


def test_router_injects_into_omni_core_when_idle(tmp_path) -> None:
    """测试目标：FollowUpRouter 经 OmniFollowUpInjector 在空闲会话注入。"""

    app, core = _omni_app(tmp_path)
    user_id = "user-omni-router"
    session_id = "sess-omni-router"
    core.open(user_id, session_id)

    router = FollowUpRouter(store=app.tool_gateway.tool_run_store, injector=OmniFollowUpInjector(core), recorder=app.recorder)
    completion = FollowUpCompletion(
        run_id="tool_run_omni_router",
        user_id=user_id,
        session_id=session_id,
        tool_name="query_route_plan",
        text="（系统通知）路线已查到：步行约 800 米。请用一句话告诉用户。",
    )
    decision = router.submit(completion)
    assert decision == "followed_up"
    _stored, provider = core._sessions[user_id]
    assert provider.followup_instructions


def test_router_queues_when_omni_response_active(tmp_path) -> None:
    """测试目标：Omni 正在回答时 late result 进入 pending queue。"""

    app, core = _omni_app(tmp_path)
    user_id = "user-omni-busy"
    session_id = "sess-omni-busy"
    core.open(user_id, session_id)
    core._active_response_sessions.add(session_id)

    router = FollowUpRouter(store=app.tool_gateway.tool_run_store, injector=OmniFollowUpInjector(core), recorder=app.recorder)
    completion = FollowUpCompletion(
        run_id="tool_run_omni_busy",
        user_id=user_id,
        session_id=session_id,
        tool_name="search_web",
        text="（系统通知）天气查到了：多云转晴。请告诉用户。",
    )
    assert router.submit(completion) == "queued"
    assert router.pending_queue.pending_count(user_id) == 1

    # response 完成后核心 flush 应注入。
    core.bind_follow_up_flush(router.flush)
    core._active_response_sessions.discard(session_id)
    core._notify_turn_completed(user_id, session_id)
    _stored, provider = core._sessions[user_id]
    assert provider.followup_instructions
    assert router.pending_queue.pending_count(user_id) == 0
