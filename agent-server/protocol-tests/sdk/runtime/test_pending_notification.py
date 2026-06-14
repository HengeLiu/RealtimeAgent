"""待通知存储与重启恢复测试（Phase 6）。

测试目标：验证 PendingNotificationStore 的消费/过期/持久化语义，以及 app 层的
重启恢复失败化、唤醒注入和 idle 关闭续期。
"""

from __future__ import annotations

import time

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.pending_notification import (
    JsonlPendingNotificationStore,
    PendingNotification,
    PendingNotificationStore,
)
from realtime_agent.tool_run import JsonlToolRunStore, ToolRun


def test_store_consume_returns_unexpired_and_marks_consumed() -> None:
    """测试目标：消费返回未过期条目并标记已消费，二次消费为空。"""

    store = PendingNotificationStore()
    store.add(PendingNotification.create(user_id="u1", session_id="s1", run_id="r1", tool_name="t", text="结果A"))
    first = store.consume_unexpired("u1")
    assert [n.text for n in first] == ["结果A"]
    assert store.consume_unexpired("u1") == []


def test_store_drops_expired_on_consume() -> None:
    """测试目标：过期条目在消费时被丢弃，不返回。"""

    store = PendingNotificationStore()
    notification = PendingNotification.create(user_id="u1", session_id="s1", run_id="r1", tool_name="t", text="过期", ttl_seconds=1)
    notification.created_at = time.time() - 10
    store.add(notification)
    assert store.consume_unexpired("u1") == []
    # 已被标记消费，不再 pending。
    assert store.list_pending("u1") == []


def test_jsonl_store_persists_and_reloads(tmp_path) -> None:
    """测试目标：JSONL 存储重启后保留未消费条目。"""

    path = tmp_path / "pending.jsonl"
    store = JsonlPendingNotificationStore(path)
    store.add(PendingNotification.create(user_id="u1", session_id="s1", run_id="r1", tool_name="t", text="持久结果"))
    reloaded = JsonlPendingNotificationStore(path)
    delivered = reloaded.consume_unexpired("u1")
    assert [n.text for n in delivered] == ["持久结果"]


def test_restart_recovery_fails_pending_runs_and_records_notification(tmp_path) -> None:
    """测试目标：重启时悬挂 Tool Run 被失败化，并生成待通知。

    测试方法：先用 JsonlToolRunStore 落一个 reported_running 运行，再启动 app 指向
    同一 tool-runs 目录。
    预期结果：运行变 failed(server_restart)，并为该用户写入待通知。
    """

    runs_root = tmp_path / "runs"
    tool_runs_dir = runs_root / "tool-runs"
    seed_store = JsonlToolRunStore(tool_runs_dir)
    run = ToolRun.create(tool_name="query_route_plan", user_id="user-recover", session_id="sess-recover", result_policy="background")
    seed_store.put(run)
    seed_store.try_transition(run.run_id, from_states={"running"}, to_state="reported_running")

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(runs_root), agent_mode="vision"))

    recovered = app.tool_gateway.tool_run_store.get(run.run_id)
    assert recovered.state == "failed"
    assert recovered.metadata.get("error", {}).get("reason") == "server_restart"
    pending = app.pending_notification_store.list_pending("user-recover")
    assert len(pending) == 1
    assert "中断" in pending[0].text


def test_wake_injection_appends_pending_notifications(tmp_path) -> None:
    """测试目标：会话打开时未过期待通知作为 user 上下文消息注入。"""

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    user_id = "user-wake"
    session_id = "sess-wake"
    app.pending_notification_store.add(
        PendingNotification.create(user_id=user_id, session_id=session_id, run_id="r1", tool_name="query_route_plan", text="（系统通知）路线已查到：步行约 800 米。")
    )

    app._open_agent_session(user_id, session_id)

    session_dir = tmp_path / "runs" / user_id / session_id
    messages_path = session_dir / "messages.jsonl"
    assert messages_path.exists()
    message_text = messages_path.read_text(encoding="utf-8")
    assert "tool_result.pending_notification" in message_text
    assert "步行约 800 米" in message_text
    # 已消费，二次打开不再注入。
    assert app.pending_notification_store.list_pending(user_id) == []


def test_idle_close_deferred_while_tool_run_active(tmp_path) -> None:
    """测试目标：存在后台 Tool Run 时 idle 关闭被推迟。"""

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    user_id = "user-idle"
    run = ToolRun.create(tool_name="query_route_plan", user_id=user_id, session_id="s", result_policy="background")
    app.tool_gateway.tool_run_store.put(run)
    app.tool_gateway.tool_run_store.try_transition(run.run_id, from_states={"running"}, to_state="reported_running")
    assert app._has_active_tool_run(user_id) is True

    # 运行终态后不再阻止关闭。
    app.tool_gateway.tool_run_store.try_transition(run.run_id, from_states={"reported_running"}, to_state="failed")
    assert app._has_active_tool_run(user_id) is False
