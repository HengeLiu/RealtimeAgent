from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from realtime_agent.tasks import BaseTask, TaskEngine, TaskRunResult, TaskSpec
from realtime_agent.tools import SystemToolContext, TaskRuntimeManagerTool, TaskStartTool, ToolExecutor


class ManagedToolTask(BaseTask):
    """测试用 TaskRuntimeManagerTool 管理的任务。"""

    task_type = "managed_tool_task"
    description = "TaskRuntimeManagerTool 测试任务"


class TimerAliasTask(BaseTask):
    """测试用 timer_task，验证 Task 启动 Tool 能归一启动真实 Task。"""

    task_type = "timer_task"
    description = "计时器任务"

    class Input(BaseModel):
        seconds: int = Field(ge=0, description="计时器时长，单位秒。")
        message: str = Field(default="", description="计时结束时播报的话。")

    input_model = Input


class SpecDeclaredStartReplyTask(BaseTask):
    """测试用显式 TaskSpec 和 TaskRunResult 任务。"""

    task_spec = TaskSpec(task_type="spec_declared_start_reply_task", start_result_timeout_seconds=1.0)
    description = "显式 TaskSpec 测试任务"

    async def run(self, context):
        """返回启动阶段的 Agent 回复建议。"""

        return TaskRunResult.started(message="后台任务已启动。", instructions="只说明任务已启动。")


def _context(engine: TaskEngine) -> SystemToolContext:
    """构造只包含 TaskEngine 的系统 Tool 上下文。"""

    return SystemToolContext(user_id="user-task-tool", session_id="sess-task-tool", devices=None, tasks=engine)


def test_task_start_tool_starts_and_runtime_manager_queries_cancels_and_lists_tasks() -> None:
    """测试目标：验证专用 TaskStartTool 启动任务，TaskRuntimeManagerTool 管理任务。

    测试方法：注册一个 Task，通过 `TaskStartTool` 创建，再通过 `task_runtime_manager`
    依次 list_types、query、list_instances、cancel。
    预期结果：启动和管理都进入 TaskEngine，返回稳定 TaskRef。
    """

    engine = TaskEngine()
    engine.register(ManagedToolTask)
    manager = TaskRuntimeManagerTool()
    starter = TaskStartTool(
        task_type="managed_tool_task",
        description=ManagedToolTask.description,
        input_model=ManagedToolTask.spec().input_model,
    )
    context = _context(engine)
    manager_schema = manager.resolved_spec().input_model.model_json_schema()
    assert "reason" not in manager_schema["properties"]

    listed = asyncio.run(manager.run(context, {"action": "list_types"}))
    assert listed.ok is True
    assert listed.data["task_types"][0]["task_type"] == "managed_tool_task"

    started = asyncio.run(
        starter.run(
            context,
            {"goal": "统一管理"},
        )
    )
    assert started.ok is True
    task_id = started.data["task_id"]
    assert started.data["state"] == "started"

    queried = asyncio.run(manager.run(context, {"action": "query", "task_id": task_id}))
    assert queried.ok is True
    assert queried.data["metadata"]["input"] == {"goal": "统一管理"}

    instances = asyncio.run(manager.run(context, {"action": "list_instances", "include_terminal": False}))
    assert instances.ok is True
    assert [item["task_id"] for item in instances.data["tasks"]] == [task_id]

    cancelled = asyncio.run(manager.run(context, {"action": "cancel", "task_id": task_id}))
    assert cancelled.ok is True
    assert cancelled.data["state"] == "cancelled"


def test_task_start_tool_uses_task_spec_and_run_agent_reply() -> None:
    """测试目标：验证 Task 可用显式 TaskSpec 声明，并通过 run() 返回 Agent 回复建议。

    测试方法：注册 `task_spec = TaskSpec(...)` 的任务，通过自动启动 Tool 创建任务。
    预期结果：registry 能识别 task_type，ToolResult.message 使用 run() 返回的建议文案。
    """

    engine = TaskEngine()
    engine.register(SpecDeclaredStartReplyTask)
    tool = TaskStartTool(
        task_type="spec_declared_start_reply_task",
        description=SpecDeclaredStartReplyTask.description,
        input_model=SpecDeclaredStartReplyTask.spec().input_model,
    )
    context = _context(engine)

    result = asyncio.run(tool.run(context, {}))

    assert result.ok is True
    assert result.message == "后台任务已启动。"
    assert result.meta["agent_reply"]["instructions"] == "只说明任务已启动。"
    assert result.data["metadata"]["task_run_result"]["ok"] is True


def test_task_start_tool_exposes_task_specific_input_schema() -> None:
    """测试目标：验证自动 Task 启动 Tool 暴露具体 Task 的输入 schema。

    测试方法：注册 timer_task，创建 `start_timer_task` wrapper 后直接启动。
    预期结果：TaskEngine 创建 timer_task，且 wrapper 的 ToolSpec 复用 Task.input_model。
    """

    engine = TaskEngine()
    engine.register(TimerAliasTask)
    tool = TaskStartTool(
        task_type="timer_task",
        description=TimerAliasTask.description,
        input_model=TimerAliasTask.spec().input_model,
    )
    context = _context(engine)

    timer = asyncio.run(
        tool.run(
            context,
            {"seconds": 60, "message": "一分钟到了"},
        )
    )

    assert tool.resolved_spec().name == "start_timer_task"
    input_schema = tool.resolved_spec().input_model.model_json_schema()
    assert input_schema["required"] == ["seconds"]
    assert input_schema["properties"]["seconds"]["description"] == "计时器时长，单位秒。"
    assert timer.ok is True
    assert timer.data["task_type"] == "timer_task"
    assert timer.data["metadata"]["input"]["seconds"] == 60
    assert timer.data["metadata"]["input"]["message"] == "一分钟到了"


def test_task_start_tool_validates_task_input_model() -> None:
    """测试目标：验证 TaskStartTool 使用和普通 Tool 相同的 Pydantic 输入校验链路。"""

    engine = TaskEngine()
    engine.register(TimerAliasTask)
    tool = TaskStartTool(
        task_type="timer_task",
        description=TimerAliasTask.description,
        input_model=TimerAliasTask.spec().input_model,
    )
    context = _context(engine)

    result = asyncio.run(ToolExecutor().execute(tool, context, {"seconds": -1}))

    assert result.ok is False
    assert result.error["code"] == "invalid_argument"


def test_task_start_tool_failure_is_model_visible() -> None:
    """测试目标：验证 Task 启动失败会返回模型可读的失败文案和操作元数据。

    测试方法：不注册目标 Task，直接通过 TaskStartTool 启动。
    预期结果：结果 ok=false，message 明确说明启动失败，meta 标记 task_start。
    """

    engine = TaskEngine()
    tool = TaskStartTool(task_type="not_registered_task", description="缺失任务")
    context = _context(engine)

    result = asyncio.run(tool.run(context, {}))

    assert result.ok is False
    assert "任务启动失败" in result.message
    assert result.error["message"] == "unknown task: not_registered_task"
    assert result.meta["operation"] == "task_start"
    assert result.meta["requested_task_type"] == "not_registered_task"
