from __future__ import annotations

import asyncio
import time

import pytest

from audio_chat.errors import AudioChatError
from audio_chat.tasks import BaseTask, TaskEngine


class TimeoutTask(BaseTask):
    """测试用超时 Task。"""

    task_type = "timeout_task"
    timeout_seconds = 0.01


class LimitedTask(BaseTask):
    """测试用并发限制 Task。"""

    task_type = "limited_task"
    max_running_per_user = 1


class CancelAwareTask(BaseTask):
    """测试用取消 Task。"""

    task_type = "cancel_aware_task"
    cancelled = False

    async def on_cancel(self, context) -> None:
        """测试目标：验证 cancel 会调用业务 Task 的 on_cancel。

        测试方法：设置类级标记。
        预期结果：取消后标记为 True，任务进入 cancelled。
        """

        type(self).cancelled = True


class FailingTask(BaseTask):
    """测试用失败 Task。"""

    task_type = "failing_task"

    async def on_start(self, context) -> None:
        """启动时抛出异常，用于验证失败流转。"""

        raise RuntimeError("boom")


class ScheduledCompleteTask(BaseTask):
    """测试用调度完成 Task。"""

    task_type = "scheduled_complete_task"

    async def on_finish(self, context, event) -> None:
        """测试目标：验证 TaskEngine 调度事件会回流到业务 Task。

        测试方法：收到由 `scheduled.done` 转换来的 `task.event.finish` 后记录结果。
        预期结果：任务最终进入 finished。
        """

        assert event.event.payload["signal_name"] == "scheduled.done"


def _wait_for_state(engine: TaskEngine, task_id: str, state: str, *, timeout_seconds: float = 0.5):
    """等待后台 TaskRunner 把任务流转到目标状态。"""

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        ref = engine.query(task_id)
        if ref.state == state:
            return ref
        time.sleep(0.01)
    return engine.query(task_id)


def test_task_query_moves_expired_started_task_to_failed() -> None:
    """测试目标：验证超时任务会流转到 failed。

    测试方法：创建带 `timeout_seconds` 的任务，等待 deadline 后查询。
    预期结果：查询触发惰性调度，任务状态变为 failed 并写入 timeout 原因。
    """

    engine = TaskEngine()
    engine.register(TimeoutTask)
    ref = asyncio.run(engine.create(task_type="timeout_task", user_id="user-timeout", session_id="sess-timeout"))

    time.sleep(0.02)
    timed_out = engine.query(ref.task_id)

    assert timed_out.state == "failed"
    assert any(signal.signal_name == "task.failed" and signal.payload.get("reason") == "timeout" for signal in engine.store.signals_for_task(ref.task_id))


def test_task_cancel_calls_on_cancel_and_records_cancelled_state() -> None:
    """测试目标：验证取消任务会调用 `on_cancel` 并进入 cancelled。

    测试方法：注册 CancelAwareTask，创建后取消。
    预期结果：业务取消钩子被调用，状态和事件都为 cancelled。
    """

    CancelAwareTask.cancelled = False
    engine = TaskEngine()
    engine.register(CancelAwareTask)
    ref = asyncio.run(engine.create(task_type="cancel_aware_task", user_id="user-cancel", session_id="sess-cancel"))

    cancelled = asyncio.run(engine.cancel(ref.task_id, reason="unit"))

    assert cancelled.state == "cancelled"
    assert CancelAwareTask.cancelled is True
    assert any(signal.signal_name == "task.cancelled" for signal in engine.store.signals_for_task(ref.task_id))


def test_task_create_failure_records_failed_event() -> None:
    """测试目标：验证启动异常会进入 failed 并暴露结构化事件。

    测试方法：Task.on_start 抛出 RuntimeError。
    预期结果：create 立即返回 started，后台 runner 把任务流转为 failed。
    """

    engine = TaskEngine()
    engine.register(FailingTask)

    async def run() -> None:
        ref = await engine.create(task_type="failing_task", user_id="user-fail", session_id="sess-fail")
        deadline = time.time() + 0.5
        while time.time() < deadline:
            if engine.query(ref.task_id).state == "failed":
                return
            await asyncio.sleep(0.01)

    asyncio.run(run())
    failed = engine.store.list_tasks()[-1]

    assert failed.state == "failed"
    assert any(signal.signal_name == "task.failed" for signal in engine.store.signals_for_task(failed.task_id))


def test_task_engine_rejects_user_concurrency_over_limit() -> None:
    """测试目标：验证同一用户运行任务数超过限制时会被拒绝。

    测试方法：注册 `max_running_per_user=1` 的 Task，连续创建两次。
    预期结果：第二次创建抛出 AudioChatError。
    """

    engine = TaskEngine()
    engine.register(LimitedTask)
    asyncio.run(engine.create(task_type="limited_task", user_id="user-limit", session_id="sess-one"))

    with pytest.raises(AudioChatError, match="concurrency exceeded"):
        asyncio.run(engine.create(task_type="limited_task", user_id="user-limit", session_id="sess-two"))


def test_task_engine_owns_and_cancels_scheduled_signals() -> None:
    """测试目标：验证 TaskEngine 统一管理延迟信号。

    测试方法：创建任务后调用 `schedule_signal()`，检查可列出，再取消该调度。
    预期结果：调度记录存在且可取消，取消后列表为空，任务不会收到到点信号。
    """

    engine = TaskEngine()
    engine.register(ScheduledCompleteTask)
    ref = asyncio.run(
        engine.create(task_type="scheduled_complete_task", user_id="user-schedule", session_id="sess-schedule")
    )

    scheduled = engine.schedule_signal(task_id=ref.task_id, signal_name="scheduled.done", delay_seconds=0.05)

    assert scheduled["task_id"] == ref.task_id
    assert [item["schedule_id"] for item in engine.list_scheduled_signals()] == [scheduled["schedule_id"]]
    assert engine.cancel_scheduled_signal(scheduled["schedule_id"]) is True
    assert engine.list_scheduled_signals() == []
    time.sleep(0.08)
    assert engine.query(ref.task_id).state == "started"


def test_task_engine_scheduled_event_flows_back_to_task() -> None:
    """测试目标：验证到点信号会转换为 Task 事件。

    测试方法：调度 `scheduled.done`，等待定时器触发。
    预期结果：Task 处理 `task.event.finish` 并完成，信号日志包含调度和 finished。
    """

    engine = TaskEngine()
    engine.register(ScheduledCompleteTask)
    ref = asyncio.run(
        engine.create(task_type="scheduled_complete_task", user_id="user-due", session_id="sess-due")
    )

    engine.schedule_signal(task_id=ref.task_id, signal_name="scheduled.done", delay_seconds=0.01)
    time.sleep(0.05)

    assert _wait_for_state(engine, ref.task_id, "finished").state == "finished"
    signal_names = [signal.signal_name for signal in engine.store.signals_for_task(ref.task_id)]
    assert "task.signal.scheduled" in signal_names
    assert "scheduled.done" in signal_names
    assert "task.finished" in signal_names
