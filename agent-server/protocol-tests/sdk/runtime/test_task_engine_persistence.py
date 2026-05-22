from __future__ import annotations

import asyncio
import time

from realtime_agent.tasks import BaseTask, JsonlTaskStore, TaskEngine


class PersistentTask(BaseTask):
    """测试用可恢复 Task。"""

    task_type = "persistent_task"
    version = "v2"
    timeout_seconds = 60


class CompletingTask(BaseTask):
    """测试用启动即完成 Task。"""

    task_type = "completing_task"

    async def on_start(self, context) -> None:
        """测试目标：验证 Task 可通过上下文进入完成态。

        测试方法：启动时调用 `context.complete()`。
        预期结果：持久化 store 重放后仍是 completed。
        """

        await context.complete({"done": True}, summary="finished")


def _wait_for_state(engine: TaskEngine, task_id: str, state: str, *, timeout_seconds: float = 0.5):
    """等待后台 TaskRunner 把任务流转到目标状态。"""

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        ref = engine.query(task_id)
        if ref.state == state:
            return ref
        time.sleep(0.01)
    return engine.query(task_id)


def test_jsonl_task_store_restores_unfinished_task(tmp_path) -> None:
    """测试目标：验证 TaskEngine 重启后可恢复未完成任务快照。

    测试方法：用 JsonlTaskStore 创建 started 任务，再用新的 store/engine 重放。
    预期结果：任务 ID、状态、版本和输入参数都被恢复。
    """

    store_root = tmp_path / "tasks"
    first = TaskEngine(store=JsonlTaskStore(store_root))
    first.register(PersistentTask)

    ref = asyncio.run(
        first.create(
            task_type="persistent_task",
            user_id="user-persist",
            session_id="sess-persist",
            input_data={"goal": "restore"},
        )
    )

    second = TaskEngine(store=JsonlTaskStore(store_root))
    second.register(PersistentTask)
    restored = second.restore_unfinished()

    assert [item.task_id for item in restored] == [ref.task_id]
    assert second.query(ref.task_id).state == "started"
    assert second.query(ref.task_id).metadata["version"] == "v2"
    assert second.query(ref.task_id).metadata["input"] == {"goal": "restore"}


def test_jsonl_task_store_keeps_terminal_state_after_restart(tmp_path) -> None:
    """测试目标：验证终态任务不会被恢复为运行态。

    测试方法：启动一个会立即完成的 Task 后重建 TaskEngine。
    预期结果：重放后状态仍为 finished，`restore_unfinished()` 不返回该任务。
    """

    store_root = tmp_path / "tasks"
    first = TaskEngine(store=JsonlTaskStore(store_root))
    first.register(CompletingTask)
    ref = asyncio.run(first.create(task_type="completing_task", user_id="user-done", session_id="sess-done"))
    ref = _wait_for_state(first, ref.task_id, "finished")

    second = TaskEngine(store=JsonlTaskStore(store_root))
    second.register(CompletingTask)

    assert ref.state == "finished"
    assert second.restore_unfinished() == []
    assert second.query(ref.task_id).state == "finished"
