"""ToolRun 取消与 tool_run_manager 测试（Phase A）。

测试目标：验证后台运行可被取消（CAS + 协程取消 + finally 清理）、取消竞态、
cancel_supported 守卫、running_message 注入、max_running_per_user 实例上限。
"""

from __future__ import annotations

import asyncio
import time

from realtime_agent.tool_run import ToolRunRunner, ToolRunStore
from realtime_agent.tools import (
    BaseTool,
    ToolContext,
    ToolDeviceFacade,
    ToolExecutor,
    ToolResult,
    ToolRunAdmin,
    ToolSpec,
)


def _context(user_id: str = "u1", session_id: str = "s1") -> ToolContext:
    return ToolContext(user_id=user_id, session_id=session_id, devices=ToolDeviceFacade(context=None))


def _make_executor(window: float = 0.1) -> ToolExecutor:
    return ToolExecutor(max_wait_timeout_seconds=window, store=ToolRunStore(), runner=ToolRunRunner())


class _CancellableTool(BaseTool):
    """长时后台工具，记录是否被取消清理。"""

    cleaned = {"value": False}

    spec = ToolSpec(
        name="cancellable_tool",
        description="可取消后台工具。",
        late_result_policy="background",
        background_timeout_seconds=60,
        cancel_supported=True,
        running_message="好的，我开始处理了。",
    )

    async def run(self, context, input_data: dict) -> ToolResult:
        try:
            await asyncio.sleep(10)
            return ToolResult.success()
        except asyncio.CancelledError:
            type(self).cleaned["value"] = True
            raise


def test_running_message_in_reported_running_result() -> None:
    """测试目标：超窗返回的 running 结果带工具自定义 running_message。"""

    executor = _make_executor()
    result = asyncio.run(executor.execute(_CancellableTool(), _context(), {}))
    assert result.status == "running"
    assert result.message == "好的，我开始处理了。"


def test_cancel_run_transitions_and_cleans_up() -> None:
    """测试目标：取消后台运行 → 状态 cancelled，协程在 finally 清理。"""

    _CancellableTool.cleaned["value"] = False
    executor = _make_executor()
    result = asyncio.run(executor.execute(_CancellableTool(), _context(), {}))
    run_id = result.data["tool_run_id"]
    assert executor.store.get(run_id).state == "reported_running"

    outcome = executor.cancel_run(run_id, reason="user_requested")
    assert outcome["ok"] is True
    assert outcome["status"] == "cancelled"
    assert executor.store.get(run_id).state == "cancelled"

    deadline = time.time() + 2.0
    while time.time() < deadline and not _CancellableTool.cleaned["value"]:
        time.sleep(0.02)
    assert _CancellableTool.cleaned["value"] is True


def test_cancel_rejected_for_non_cancellable_tool() -> None:
    """测试目标：未声明 cancel_supported 的工具取消请求被拒绝。"""

    class _NoCancelTool(BaseTool):
        spec = ToolSpec(
            name="no_cancel_tool",
            description="不可取消后台工具。",
            late_result_policy="background",
            background_timeout_seconds=60,
        )

        async def run(self, context, input_data: dict) -> ToolResult:
            await asyncio.sleep(10)
            return ToolResult.success()

    executor = _make_executor()
    result = asyncio.run(executor.execute(_NoCancelTool(), _context(), {}))
    run_id = result.data["tool_run_id"]
    outcome = executor.cancel_run(run_id)
    assert outcome["ok"] is False
    assert outcome["status"] == "not_cancellable"
    assert executor.store.get(run_id).state == "reported_running"


def test_cancel_too_late_for_completed_run() -> None:
    """测试目标：已完成的运行取消返回 too_late。"""

    class _QuickBgTool(BaseTool):
        spec = ToolSpec(
            name="quick_bg_tool",
            description="快速后台工具。",
            late_result_policy="background",
            background_timeout_seconds=60,
            cancel_supported=True,
        )

        async def run(self, context, input_data: dict) -> ToolResult:
            return ToolResult.success(message="done")

    executor = _make_executor(window=1.0)
    result = asyncio.run(executor.execute(_QuickBgTool(), _context(), {}))
    # 窗口内完成，状态 completed_inline。
    run_id = (result.data or {}).get("tool_run_id")
    if run_id is None:
        # completed_inline 直接返回最终结果，没有 run_id 暴露；用 store 找。
        run_id = executor.store.list_runs()[0].run_id
    outcome = executor.cancel_run(run_id)
    assert outcome["ok"] is False
    assert outcome["status"] in {"too_late", "not_found"}


def test_cancel_not_found() -> None:
    """测试目标：取消不存在的运行返回 not_found。"""

    executor = _make_executor()
    outcome = executor.cancel_run("tool_run_missing")
    assert outcome["ok"] is False
    assert outcome["status"] == "not_found"


def test_max_running_per_user_allows_n_instances() -> None:
    """测试目标：max_running_per_user 允许配置上限内的多个并发实例。"""

    class _MultiTool(BaseTool):
        spec = ToolSpec(
            name="multi_tool",
            description="允许多实例后台工具。",
            late_result_policy="background",
            background_timeout_seconds=60,
            max_running_per_user=2,
        )

        async def run(self, context, input_data: dict) -> ToolResult:
            await asyncio.sleep(5)
            return ToolResult.success()

    executor = _make_executor()
    tool = _MultiTool()
    first = asyncio.run(executor.execute(tool, _context(), {}))
    second = asyncio.run(executor.execute(tool, _context(), {}))
    # 两个不同的运行（未触发去重）。
    assert first.data["tool_run_id"] != second.data["tool_run_id"]
    # 第三个触发上限去重，复用既有运行。
    third = asyncio.run(executor.execute(tool, _context(), {}))
    assert third.data["tool_run_id"] in {first.data["tool_run_id"], second.data["tool_run_id"]}


def test_tool_run_admin_list_query_cancel() -> None:
    """测试目标：ToolRunAdmin 提供 list/query/cancel 视图。"""

    _CancellableTool.cleaned["value"] = False
    executor = _make_executor()
    admin = ToolRunAdmin(store=executor.store, executor=executor)
    result = asyncio.run(executor.execute(_CancellableTool(), _context(), {}))
    run_id = result.data["tool_run_id"]

    rows = admin.list_instances(user_id="u1")
    assert any(row["tool_run_id"] == run_id for row in rows)
    row = admin.query(run_id)
    assert row is not None and row["cancel_supported"] is True
    outcome = admin.cancel(run_id)
    assert outcome["ok"] is True
    assert admin.query(run_id)["state"] == "cancelled"
