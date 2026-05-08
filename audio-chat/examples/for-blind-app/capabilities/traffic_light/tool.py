from __future__ import annotations

from pydantic import BaseModel, Field

from audio_chat import BaseTool, ToolContext, ToolResult, ToolSpec


class StartTrafficLightInput(BaseModel):
    """红绿灯识别任务输入参数。"""

    expected_state: str | None = Field(default=None, description="用户期望关注的红绿灯状态，例如 green 或 red。")
    frame_limit: int = Field(default=3, ge=1, le=60, description="本次任务最多分析的图片帧数。")


class StartTrafficLightOutput(BaseModel):
    """红绿灯识别任务输出结构。"""

    started: bool = Field(description="是否成功创建任务。")
    task_id: str | None = Field(default=None, description="任务 ID。")
    state: str | None = Field(default=None, description="任务状态。")
    reason: str | None = Field(default=None, description="未启动原因。")


class StartTrafficLightTool(BaseTool):
    """启动红绿灯视觉任务的 Tool。"""

    spec = ToolSpec(
        name="start_traffic_light",
        description="启动红绿灯识别任务。",
        input_model=StartTrafficLightInput,
        output_model=StartTrafficLightOutput,
        progress_message="正在启动红绿灯识别",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """创建红绿灯识别 Task。

        主要逻辑：Tool 只创建 server 侧 Task；端侧采集由 Task 通过 event + stream 配置。
        参数：`input_data` 可包含 `expected_state`、`frame_limit`。
        返回值：包含任务引用。
        异常情况：TaskEngine 未配置时返回未启动结果。
        """

        if context.tasks is None:
            return ToolResult.success(data={"started": False, "reason": "task_engine_unavailable"})
        ref = await context.tasks.create(
            task_type="traffic_light_task",
            user_id=context.user_id,
            session_id=context.session_id,
            input_data=dict(input_data),
            summary="红绿灯识别任务",
        )
        return ToolResult.success(data={"started": True, "task_id": ref.task_id, "state": ref.state}, tasks=[ref])
