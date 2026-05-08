from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from audio_chat import BaseTool, ToolContext, ToolResult, ToolSpec


class TimerInput(BaseModel):
    """计时器 Tool 输入参数。"""

    action: Literal["create", "query", "cancel"] = Field(default="create", description="计时器操作类型。")
    seconds: int = Field(default=60, ge=0, le=86400, description="创建计时器时的秒数，0 用于回放立即到点。")
    task_id: str | None = Field(default=None, description="查询或取消时使用的任务 ID。")
    auto_fire: bool = Field(default=True, description="是否在创建后立即调度到点事件，回放测试可关闭。")


class TimerOutput(BaseModel):
    """计时器 Tool 输出结构。"""

    task_id: str | None = Field(default=None, description="任务 ID。")
    state: str | None = Field(default=None, description="任务状态。")
    ok: bool | None = Field(default=None, description="是否完成操作。")
    reason: str | None = Field(default=None, description="未执行原因。")


class TimerTool(BaseTool):
    """计时器 Tool。

    主要功能：提供创建、查询、取消计时器的统一样板入口。
    """

    spec = ToolSpec(
        name="timer",
        description="创建、查询或取消计时器。",
        input_model=TimerInput,
        output_model=TimerOutput,
        progress_message="正在处理计时器",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行计时器操作。

        主要逻辑：
        1. `action=create` 创建 `timer_task`。
        2. `action=query` 查询任务状态。
        3. `action=cancel` 取消任务。

        参数：`input_data` 包含 `action`、`seconds` 或 `task_id`。
        返回值：任务引用或状态。
        异常情况：TaskEngine 未配置时返回未执行结果。
        """

        if context.tasks is None:
            return ToolResult.success(data={"ok": False, "reason": "task_engine_unavailable"})
        action = input_data["action"]
        if action == "query":
            ref = context.tasks.query(str(input_data.get("task_id") or ""))
            return ToolResult.success(data=ref.__dict__, tasks=[ref], message=ref.state)
        if action == "cancel":
            ref = await context.tasks.cancel(str(input_data.get("task_id") or ""), reason="tool_requested")
            return ToolResult.success(data=ref.__dict__, tasks=[ref], message=ref.state)
        ref = await context.tasks.create(
            task_type="timer_task",
            user_id=context.user_id,
            session_id=context.session_id,
            input_data=dict(input_data),
            summary="计时器任务",
        )
        return ToolResult.success(data={"task_id": ref.task_id, "state": ref.state}, tasks=[ref], message="计时器已创建")
