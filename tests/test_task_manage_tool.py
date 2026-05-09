from __future__ import annotations

import asyncio

from audio_chat.tasks import BaseTask, TaskEngine
from audio_chat.tools import SystemToolContext, TaskRuntimeManagerTool


class ManagedToolTask(BaseTask):
    """测试用 TaskRuntimeManagerTool 管理的任务。"""

    task_type = "managed_tool_task"
    description = "TaskRuntimeManagerTool 测试任务"


def _context(engine: TaskEngine) -> SystemToolContext:
    """构造只包含 TaskEngine 的系统 Tool 上下文。"""

    return SystemToolContext(user_id="user-task-tool", session_id="sess-task-tool", devices=None, tasks=engine)


def test_task_runtime_manager_tool_starts_queries_cancels_and_lists_tasks() -> None:
    """测试目标：验证统一 TaskRuntimeManagerTool 覆盖 Task 的主要管理动作。

    测试方法：注册一个 Task，通过 `task_runtime_manager` 依次 list_types、start、query、list_instances、cancel。
    预期结果：所有动作都通过同一个 Tool 进入 TaskEngine，返回稳定 TaskRef。
    """

    engine = TaskEngine()
    engine.register(ManagedToolTask)
    tool = TaskRuntimeManagerTool()
    context = _context(engine)

    listed = asyncio.run(tool.run(context, {"action": "list_types"}))
    assert listed.ok is True
    assert listed.data["task_types"][0]["task_type"] == "managed_tool_task"

    started = asyncio.run(
        tool.run(
            context,
            {
                "action": "start",
                "task_type": "managed_tool_task",
                "input_data": {"goal": "统一管理"},
                "summary": "统一任务",
            },
        )
    )
    assert started.ok is True
    task_id = started.data["task_id"]
    assert started.data["state"] == "running"

    queried = asyncio.run(tool.run(context, {"action": "query", "task_id": task_id}))
    assert queried.ok is True
    assert queried.data["metadata"]["input"] == {"goal": "统一管理"}

    instances = asyncio.run(tool.run(context, {"action": "list_instances", "include_terminal": False}))
    assert instances.ok is True
    assert [item["task_id"] for item in instances.data["tasks"]] == [task_id]

    cancelled = asyncio.run(tool.run(context, {"action": "cancel", "task_id": task_id, "reason": "unit"}))
    assert cancelled.ok is True
    assert cancelled.data["state"] == "cancelled"
