"""TimerTool 迁移测试（Phase C）。

测试目标：验证计时器作为 background Tool 的等待窗口、到点 direct 播报、按秒数定预算、
取消、以及别名入参归一。
"""

from __future__ import annotations

import asyncio
import time

from realtime_agent.tool_run import ToolRunRunner, ToolRunStore
from realtime_agent.tools import (
    TimerInput,
    TimerTool,
    ToolContext,
    ToolDeviceFacade,
    ToolExecutor,
)


def _context(user_id: str = "u1", session_id: str = "s1") -> ToolContext:
    return ToolContext(user_id=user_id, session_id=session_id, devices=ToolDeviceFacade(context=None))


def test_timer_input_normalizes_aliases() -> None:
    """测试目标：duration/delay/timeout_seconds 与 notify_text/text 别名归一。"""

    model = TimerInput.model_validate({"duration_seconds": 90, "notify_text": "喝水"})
    assert model.seconds == 90
    assert model.message == "喝水"


def test_timer_tool_spec_is_background_direct_cancellable() -> None:
    """测试目标：TimerTool 声明 background + direct + 可取消。"""

    spec = TimerTool().resolved_spec()
    assert spec.late_result_policy == "background"
    assert spec.late_result_notify == "direct"
    assert spec.cancel_supported is True
    assert spec.running_message == "计时器已开始计时。"


def test_timer_background_budget_tracks_seconds() -> None:
    """测试目标：后台预算随计时秒数增长，避免到点前被强制取消。"""

    tool = TimerTool()
    assert tool.background_timeout_seconds_for({"seconds": 120}) == 150.0


def test_timer_reports_running_then_fires() -> None:
    """测试目标：超窗计时返回 running，到点后后台完成返回到点文案。"""

    executor = ToolExecutor(max_wait_timeout_seconds=0.1, store=ToolRunStore(), runner=ToolRunRunner())
    fired: list = []
    executor.on_background_complete = lambda run, result: fired.append(result.message)
    result = asyncio.run(executor.execute(TimerTool(), _context(), {"seconds": 1, "message": "一秒到了。"}))
    assert result.status == "running"
    assert result.message == "计时器已开始计时。"
    run_id = result.data["tool_run_id"]

    deadline = time.time() + 3.0
    while time.time() < deadline and not executor.store.get(run_id).is_terminal:
        time.sleep(0.02)
    assert fired == ["一秒到了。"]
    assert executor.store.get(run_id).result["message"] == "一秒到了。"


def test_timer_cancel_stops_before_firing() -> None:
    """测试目标：取消计时器后到点不再触发，运行进入 cancelled。"""

    executor = ToolExecutor(max_wait_timeout_seconds=0.1, store=ToolRunStore(), runner=ToolRunRunner())
    fired: list = []
    executor.on_background_complete = lambda run, result: fired.append(result.message)
    result = asyncio.run(executor.execute(TimerTool(), _context(), {"seconds": 5, "message": "五秒到了。"}))
    run_id = result.data["tool_run_id"]
    outcome = executor.cancel_run(run_id)
    assert outcome["ok"] is True
    assert executor.store.get(run_id).state == "cancelled"
    time.sleep(0.3)
    assert fired == []
