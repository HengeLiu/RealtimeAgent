"""Tool Run 对象模型、状态机与存储测试（Phase 1）。

测试目标：验证 ToolRun 状态机的合法迁移、终态不可回退、CAS 并发裁决，
以及 JsonlToolRunStore 的落盘重放与 ToolSpec late result 策略校验。
"""

from __future__ import annotations

import threading

import pytest

from realtime_agent.errors import RealtimeAgentError
from realtime_agent.tool_run import (
    JsonlToolRunStore,
    ToolRun,
    ToolRunError,
    ToolRunStateMachine,
    ToolRunStore,
)
from realtime_agent.tools import (
    BaseTool,
    ToolError,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


class _BackgroundTool(BaseTool):
    """声明 background 策略的样板工具。"""

    spec = ToolSpec(
        name="bg_tool",
        description="后台能力工具。",
        late_result_policy="background",
        background_timeout_seconds=60,
        follow_up_ttl_seconds=300,
    )

    async def run(self, context, input_data: dict) -> ToolResult:
        return ToolResult.success(data=input_data)


def test_state_machine_allows_documented_transitions() -> None:
    """测试目标：状态机允许设计文档列出的迁移。

    测试方法：逐一断言 running/reported_running/completed_late 的合法目标。
    预期结果：合法迁移返回 True。
    """

    assert ToolRunStateMachine.can_transition("running", "completed_inline")
    assert ToolRunStateMachine.can_transition("running", "reported_running")
    assert ToolRunStateMachine.can_transition("running", "failed")
    assert ToolRunStateMachine.can_transition("reported_running", "completed_late")
    assert ToolRunStateMachine.can_transition("reported_running", "failed")
    assert ToolRunStateMachine.can_transition("completed_late", "followed_up")
    assert ToolRunStateMachine.can_transition("completed_late", "expired")


def test_state_machine_rejects_terminal_and_illegal() -> None:
    """测试目标：终态不可回退，跨状态非法迁移被拒绝。

    测试方法：尝试从终态和非相邻状态迁移。
    预期结果：can_transition 返回 False，validate 抛出 ToolRunError。
    """

    assert not ToolRunStateMachine.can_transition("completed_inline", "running")
    assert not ToolRunStateMachine.can_transition("failed", "completed_late")
    assert not ToolRunStateMachine.can_transition("running", "followed_up")
    with pytest.raises(ToolRunError):
        ToolRunStateMachine.validate("completed_inline", "running")


def test_store_try_transition_cas_single_winner() -> None:
    """测试目标：并发 CAS 迁移只有一个成功。

    测试方法：多线程同时从 running 推进到 completed_inline / reported_running，
    统计成功次数。
    预期结果：恰好一次成功，最终状态唯一。
    """

    store = ToolRunStore()
    run = ToolRun.create(
        tool_name="bg_tool",
        user_id="u1",
        session_id="s1",
        result_policy="background",
    )
    store.put(run)

    results: list[bool] = []
    barrier = threading.Barrier(2)

    def _inline() -> None:
        barrier.wait()
        results.append(
            store.try_transition(run.run_id, from_states={"running"}, to_state="completed_inline")
        )

    def _reported() -> None:
        barrier.wait()
        results.append(
            store.try_transition(run.run_id, from_states={"running"}, to_state="reported_running")
        )

    threads = [threading.Thread(target=_inline), threading.Thread(target=_reported)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(True) == 1
    assert store.get(run.run_id).state in {"completed_inline", "reported_running"}


def test_store_try_transition_records_result_and_followup() -> None:
    """测试目标：CAS 推进时写入结果和 follow-up 决策。

    测试方法：running->reported_running->completed_late->followed_up，逐步带数据。
    预期结果：结果与 follow-up 字段被合并保存。
    """

    store = ToolRunStore()
    run = ToolRun.create(tool_name="bg_tool", user_id="u1", session_id="s1", result_policy="background")
    store.put(run)

    assert store.try_transition(run.run_id, from_states={"running"}, to_state="reported_running")
    assert store.try_transition(
        run.run_id,
        from_states={"reported_running"},
        to_state="completed_late",
        result={"ok": True, "data": {"x": 1}},
    )
    assert store.try_transition(
        run.run_id,
        from_states={"completed_late"},
        to_state="followed_up",
        follow_up={"decision": "followed_up", "channel": "vl_turn"},
    )
    saved = store.get(run.run_id)
    assert saved.state == "followed_up"
    assert saved.result == {"ok": True, "data": {"x": 1}}
    assert saved.follow_up["channel"] == "vl_turn"


def test_store_find_active_by_tool_dedupe() -> None:
    """测试目标：去重查询只命中后台等待回流的同名运行。

    测试方法：构造 reported_running 与 completed_inline 两个同名运行。
    预期结果：只返回 reported_running 的那一个。
    """

    store = ToolRunStore()
    active = ToolRun.create(tool_name="bg_tool", user_id="u1", session_id="s1", result_policy="background")
    store.put(active)
    store.try_transition(active.run_id, from_states={"running"}, to_state="reported_running")

    finished = ToolRun.create(tool_name="bg_tool", user_id="u1", session_id="s1", result_policy="background")
    store.put(finished)
    store.try_transition(finished.run_id, from_states={"running"}, to_state="completed_inline")

    found = store.find_active_by_tool(user_id="u1", session_id="s1", tool_name="bg_tool")
    assert found is not None
    assert found.run_id == active.run_id


def test_jsonl_store_replays_latest_state(tmp_path) -> None:
    """测试目标：JSONL 存储重放后恢复最新状态。

    测试方法：写入并多次迁移后，用新 store 实例从同一目录加载。
    预期结果：恢复的运行状态与结果与落盘前一致。
    """

    store = JsonlToolRunStore(tmp_path)
    run = ToolRun.create(tool_name="bg_tool", user_id="u1", session_id="s1", result_policy="background")
    store.put(run)
    store.try_transition(run.run_id, from_states={"running"}, to_state="reported_running")
    store.try_transition(
        run.run_id,
        from_states={"reported_running"},
        to_state="completed_late",
        result={"ok": True},
    )

    reloaded = JsonlToolRunStore(tmp_path)
    restored = reloaded.get(run.run_id)
    assert restored.state == "completed_late"
    assert restored.result == {"ok": True}
    assert restored.run_id == run.run_id


def test_jsonl_store_lists_non_terminal(tmp_path) -> None:
    """测试目标：列出未终态运行，用于重启恢复扫描。

    测试方法：一个停在 reported_running，一个推进到 failed。
    预期结果：只列出 reported_running。
    """

    store = JsonlToolRunStore(tmp_path)
    pending = ToolRun.create(tool_name="bg_tool", user_id="u1", session_id="s1", result_policy="background")
    store.put(pending)
    store.try_transition(pending.run_id, from_states={"running"}, to_state="reported_running")

    failed = ToolRun.create(tool_name="bg_tool", user_id="u1", session_id="s1", result_policy="background")
    store.put(failed)
    store.try_transition(failed.run_id, from_states={"running"}, to_state="failed")

    non_terminal = JsonlToolRunStore(tmp_path).list_non_terminal()
    assert [run.run_id for run in non_terminal] == [pending.run_id]


def test_registry_accepts_background_tool() -> None:
    """测试目标：合法 background 工具可注册。

    测试方法：注册声明 background 且 background_timeout 大于窗口的工具。
    预期结果：注册成功，可取回。
    """

    registry = ToolRegistry()
    registry.register(_BackgroundTool())
    assert registry.get("bg_tool").resolved_spec().late_result_policy == "background"


def test_registry_rejects_background_for_forbidden_tool() -> None:
    """测试目标：禁止后台化的工具声明 background 时注册失败。

    测试方法：构造名为 capture_photo 的 background 工具。
    预期结果：注册抛出 ToolError。
    """

    class _CaptureBg(BaseTool):
        spec = ToolSpec(
            name="capture_photo",
            description="拍照。",
            late_result_policy="background",
            background_timeout_seconds=60,
        )

        async def run(self, context, input_data: dict) -> ToolResult:
            return ToolResult.success()

    registry = ToolRegistry()
    with pytest.raises(ToolError):
        registry.register(_CaptureBg())


def test_registry_rejects_short_background_timeout() -> None:
    """测试目标：background_timeout 不大于窗口时注册失败。

    测试方法：声明 background_timeout_seconds=1（小于 3 秒窗口）。
    预期结果：注册抛出 ToolError。
    """

    class _ShortBg(BaseTool):
        spec = ToolSpec(
            name="short_bg_tool",
            description="后台超时过短。",
            late_result_policy="background",
            background_timeout_seconds=1,
        )

        async def run(self, context, input_data: dict) -> ToolResult:
            return ToolResult.success()

    registry = ToolRegistry()
    with pytest.raises(ToolError):
        registry.register(_ShortBg())
