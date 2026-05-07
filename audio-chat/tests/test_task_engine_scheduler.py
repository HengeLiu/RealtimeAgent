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


def test_task_query_moves_expired_running_task_to_timeout() -> None:
    """测试目标：验证超时任务会流转到 timeout。

    测试方法：创建带 `timeout_seconds` 的任务，等待 deadline 后查询。
    预期结果：查询触发惰性调度，任务状态变为 timeout 并写入 timeout 事件。
    """

    engine = TaskEngine()
    engine.register(TimeoutTask)
    ref = asyncio.run(engine.create(task_type="timeout_task", user_id="user-timeout", session_id="sess-timeout"))

    time.sleep(0.02)
    timed_out = engine.query(ref.task_id)

    assert timed_out.state == "timeout"
    assert any(event.event_name == "task.timeout" for event in engine.store.events_for_task(ref.task_id))


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
    assert any(event.event_name == "task.cancelled" for event in engine.store.events_for_task(ref.task_id))


def test_task_create_failure_records_failed_event() -> None:
    """测试目标：验证启动异常会进入 failed 并暴露结构化事件。

    测试方法：Task.on_start 抛出 RuntimeError。
    预期结果：create 向上抛出异常，store 中最后一个任务为 failed。
    """

    engine = TaskEngine()
    engine.register(FailingTask)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(engine.create(task_type="failing_task", user_id="user-fail", session_id="sess-fail"))

    failed = engine.store.list_tasks()[-1]
    assert failed.state == "failed"
    assert any(event.event_name == "task.failed" for event in engine.store.events_for_task(failed.task_id))


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
