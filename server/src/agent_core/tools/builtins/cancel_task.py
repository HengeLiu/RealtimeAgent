"""取消任务 Tool。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_core.context.models import TaskRef
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTaskTool
from infra.errors import ErrorCode, build_error


class CancelTaskInput(BaseModel):
    """取消任务输入。"""

    task_id: str = Field(description="任务编号")


class CancelTaskOutput(BaseModel):
    """取消任务输出。"""

    task_id: str
    state: str
    summary: str


class CancelTaskTool(BaseTaskTool):
    """取消后台任务。"""

    spec = ToolSpec(
        name="cancel_task",
        description="取消指定后台任务",
        input_model=CancelTaskInput,
        output_model=CancelTaskOutput,
        capability_type="task",
        tags=["task", "cancel"],
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
