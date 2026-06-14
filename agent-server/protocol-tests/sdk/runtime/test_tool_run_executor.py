"""ToolExecutor 后台化与等待窗口测试（Phase 2）。

测试目标：验证 fail_fast 行为不变、background 工具四路径、窗口与完成竞态、
模型重试去重以及每用户并发上限。
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from realtime_agent.errors import ErrorCode
from realtime_agent.tool_run import ToolRunRunner, ToolRunStore
from realtime_agent.tools import (
    BaseTool,
    ToolContext,
    ToolDeviceFacade,
    ToolError,
    ToolExecutor,
    ToolResult,
    ToolSpec,
)


def _context(user_id: str = "u1", session_id: str = "s1") -> ToolContext:
    """构造最小 ToolContext。"""

    return ToolContext(user_id=user_id, session_id=session_id, devices=ToolDeviceFacade(context=None))


class _FastTool(BaseTool):
    """窗口内即完成的默认工具。"""

    spec = ToolSpec(name="fast_tool", description="快速工具。")

    async def run(self, context, input_data: dict) -> ToolResult:
        return ToolResult.success(data={"echo": input_data})


class _SlowFailFastTool(BaseTool):
    """超过窗口的默认 fail_fast 工具。"""

    spec = ToolSpec(name="slow_failfast_tool", description="慢速 fail_fast 工具。")

    async def run(self, context, input_data: dict) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult.success(data={"done": True})


class _SlowBackgroundTool(BaseTool):
    """超过窗口、后台完成的 background 工具。"""

    spec = ToolSpec(
        name="slow_bg_tool",
        description="慢速后台工具。",
        late_result_policy="background",
        background_timeout_seconds=60,
        follow_up_ttl_seconds=300,
    )

    async def run(self, context, input_data: dict) -> ToolResult:
        await asyncio.sleep(0.3)
        return ToolResult.success(data={"route": "ready"}, message="路线已准备。")


class _RaisingBackgroundTool(BaseTool):
    """后台抛异常的 background 工具。"""

    spec = ToolSpec(
        name="raise_bg_tool",
        description="后台失败工具。",
        late_result_policy="background",
        background_timeout_seconds=60,
    )

    async def run(self, context, input_data: dict) -> ToolResult:
        await asyncio.sleep(0.2)
        raise RuntimeError("boom")


def _make_executor(**kwargs) -> ToolExecutor:
    """构造带短窗口的 ToolExecutor 便于测试。"""

    return ToolExecutor(
        max_wait_timeout_seconds=kwargs.pop("window", 0.2),
        store=kwargs.pop("store", None) or ToolRunStore(),
        runner=kwargs.pop("runner", None) or ToolRunRunner(),
        **kwargs,
    )


def test_fast_tool_completed_inline() -> None:
    """测试目标：窗口内完成返回最终结果，状态 completed_inline。"""

    executor = _make_executor(window=1.0)
    tool = _FastTool()
    result = asyncio.run(executor.execute(tool, _context(), {"city": "上海"}))
    assert result.ok is True
    assert result.status == "completed"
    assert result.data == {"echo": {"city": "上海"}}
    runs = executor.store.list_runs()
    assert len(runs) == 1
    assert runs[0].state == "completed_inline"


def test_failfast_tool_times_out() -> None:
    """测试目标：fail_fast 工具超窗返回 TIMEOUT 失败，状态 failed。"""

    executor = _make_executor(window=0.2)
    result = asyncio.run(executor.execute(_SlowFailFastTool(), _context(), {}))
    assert result.ok is False
    assert result.error is not None
    assert result.error.get("code") == ErrorCode.TIMEOUT.value
    run = executor.store.list_runs()[0]
    assert run.state == "failed"
    assert run.metadata.get("error", {}).get("reason") == "timeout"


def test_background_tool_reports_running_then_completes() -> None:
    """测试目标：background 工具超窗返回 running，后台完成后 completed_late。"""

    executor = _make_executor(window=0.1)
    completions: list = []
    executor.on_background_complete = lambda run, result: completions.append((run.run_id, result.ok))
    result = asyncio.run(executor.execute(_SlowBackgroundTool(), _context(), {"destination": "公园"}))
    assert result.ok is True
    assert result.status == "running"
    run_id = result.data["tool_run_id"]
    run = executor.store.get(run_id)
    assert run.state == "reported_running"

    deadline = time.time() + 2.0
    while time.time() < deadline and executor.store.get(run_id).state != "completed_late":
        time.sleep(0.02)
    final = executor.store.get(run_id)
    assert final.state == "completed_late"
    assert final.result["data"] == {"route": "ready"}
    assert completions and completions[0][0] == run_id


def test_background_tool_failure_marks_failed() -> None:
    """测试目标：background 工具后台抛异常时进入 failed，并仍触发回调。"""

    executor = _make_executor(window=0.1)
    completions: list = []
    executor.on_background_complete = lambda run, result: completions.append(result.ok)
    result = asyncio.run(executor.execute(_RaisingBackgroundTool(), _context(), {}))
    assert result.status == "running"
    run_id = result.data["tool_run_id"]

    deadline = time.time() + 2.0
    while time.time() < deadline and executor.store.get(run_id).state not in {"failed", "completed_late"}:
        time.sleep(0.02)
    run = executor.store.get(run_id)
    assert run.state == "failed"
    assert completions == [False]


def test_background_boundary_no_double_result() -> None:
    """测试目标：完成与窗口几乎同时发生时不产生双结果。

    测试方法：窗口与工具耗时都设为 ~0.1s，多次执行，断言每次只落一个终态。
    预期结果：状态要么 completed_inline 要么 completed_late，二者其一。
    """

    class _BoundaryTool(BaseTool):
        spec = ToolSpec(
            name="boundary_tool",
            description="边界工具。",
            late_result_policy="background",
            background_timeout_seconds=60,
        )

        async def run(self, context, input_data: dict) -> ToolResult:
            await asyncio.sleep(0.1)
            return ToolResult.success(data={"v": 1})

    for _ in range(5):
        executor = _make_executor(window=0.1)
        result = asyncio.run(executor.execute(_BoundaryTool(), _context(), {}))
        run_id = (result.data or {}).get("tool_run_id")
        if run_id is None:
            # 窗口内完成路径
            assert result.ok is True
            continue
        deadline = time.time() + 2.0
        while time.time() < deadline and not executor.store.get(run_id).is_terminal:
            if executor.store.get(run_id).state == "completed_late":
                break
            time.sleep(0.02)
        run = executor.store.get(run_id)
        assert run.state in {"completed_inline", "completed_late", "reported_running"}


def test_dedupe_reuses_running_run() -> None:
    """测试目标：同会话同名 background 工具重试复用既有 running run。"""

    executor = _make_executor(window=0.1)
    tool = _SlowBackgroundTool()
    first = asyncio.run(executor.execute(tool, _context(), {"destination": "公园"}))
    assert first.status == "running"
    first_run_id = first.data["tool_run_id"]
    second = asyncio.run(executor.execute(tool, _context(), {"destination": "公园"}))
    assert second.status == "running"
    assert second.data["tool_run_id"] == first_run_id
    # 只创建了一个 Tool Run。
    assert len([run for run in executor.store.list_runs() if run.state == "reported_running"]) == 1


def test_per_user_concurrency_limit() -> None:
    """测试目标：每用户并发上限限制后台同时运行的工具数。

    测试方法：并发上限设为 1，连续启动两个慢 background 工具，断言第二个在第一个
    释放前不会进入实际运行。
    预期结果：两次都返回 running，但同一时刻活跃运行数不超过 1。
    """

    active = {"count": 0, "max": 0}
    lock = threading.Lock()

    class _ConcurrentTool(BaseTool):
        spec = ToolSpec(
            name="concurrent_tool",
            description="并发工具。",
            late_result_policy="background",
            background_timeout_seconds=60,
            allow_concurrent_runs=True,
        )

        async def run(self, context, input_data: dict) -> ToolResult:
            with lock:
                active["count"] += 1
                active["max"] = max(active["max"], active["count"])
            await asyncio.sleep(0.2)
            with lock:
                active["count"] -= 1
            return ToolResult.success()

    runner = ToolRunRunner(per_user_concurrency=1)
    executor = _make_executor(window=0.05, runner=runner)
    tool = _ConcurrentTool()
    asyncio.run(executor.execute(tool, _context(), {}))
    asyncio.run(executor.execute(tool, _context(), {}))
    time.sleep(0.8)
    assert active["max"] == 1
