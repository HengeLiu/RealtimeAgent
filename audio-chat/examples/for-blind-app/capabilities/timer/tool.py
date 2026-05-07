from __future__ import annotations

from audio_chat import BaseTool, ToolContext, ToolResult


class TimerTool(BaseTool):
    """计时器 Tool。

    主要功能：提供创建、查询、取消计时器的统一样板入口。
    """

    name = "timer"
    description = "创建、查询或取消计时器"
    progress_message = "正在处理计时器"

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
        action = str(input_data.get("action") or "create").strip().lower()
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
