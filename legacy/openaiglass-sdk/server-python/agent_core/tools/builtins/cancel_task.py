"""取消任务 Tool。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_core.context.models import TaskRef
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTaskTool
from infra.errors import ErrorCode, build_error


class CancelTaskInput(BaseModel):
    """取消任务输入。"""

    task_id: str = Field(
        description="要取消的后台任务编号，通常来自此前工具返回的 task_id；没有明确任务编号时不要猜测。",
    )


class CancelTaskOutput(BaseModel):
    """取消任务输出。"""

    task_id: str
    state: str
    summary: str


class CancelTaskTool(BaseTaskTool):
    """取消后台任务。"""

    spec = ToolSpec(
        name="cancel_task",
        description="当用户明确要求停止、取消或结束某个正在运行的后台任务时调用；只适用于已经有 task_id 的任务。",
        input_model=CancelTaskInput,
        output_model=CancelTaskOutput,
        capability_type="task",
        tags=["task", "cancel"],
        progress_message=[
            "我先帮你停止这个任务。",
            "好，我来结束这个任务。",
            "稍等，我正在取消任务。",
        ],
    )

    def run(self, context: AgentToolContext, input_data: CancelTaskInput) -> CapabilityResult:
        if context.task_gateway is None:
            raise build_error(ErrorCode.INVALID_CONFIG, "TaskGateway 未配置，无法取消任务")
        runtime = context.task_gateway.cancel_task(input_data.task_id)
        summary = f"任务 {runtime.task_id} 已取消。"
        task_ref = TaskRef(
            task_id=runtime.task_id,
            task_type=runtime.task_type,
            state=runtime.state,
            summary=summary,
        )
        return CapabilityResult.success(
            data={
                "task_id": runtime.task_id,
                "state": runtime.state,
                "summary": summary,
            },
            message=summary,
            task_refs=[task_ref],
        )
