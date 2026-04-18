"""创建计时器任务 Tool。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_core.context.models import TaskRef
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTaskTool
from infra.errors import ErrorCode, build_error


class CreateTimerInput(BaseModel):
    """创建计时器输入。"""

    duration_seconds: int = Field(description="倒计时秒数", gt=0)
    label: str | None = Field(default=None, description="任务标签")


class CreateTimerOutput(BaseModel):
    """创建计时器输出。"""

    task_id: str
    task_type: str
    state: str
    summary: str


class CreateTimerTool(BaseTaskTool):
    """创建最小 timer_task。"""

    spec = ToolSpec(
        name="create_timer",
        description="创建一个新的计时器任务",
        input_model=CreateTimerInput,
        output_model=CreateTimerOutput,
        capability_type="task",
        tags=["task", "timer"],
    )

    def run(self, context: AgentToolContext, input_data: CreateTimerInput) -> CapabilityResult:
        if context.task_gateway is None:
            raise build_error(ErrorCode.INVALID_CONFIG, "TaskGateway 未配置，无法创建任务")
        runtime = context.task_gateway.create_task(
            task_type="timer_task",
            session_id=context.session_id,
            device_id=context.device_id,
            input_data={
                "duration_seconds": input_data.duration_seconds,
                "label": input_data.label,
            },
        )
        summary = f"已创建 {input_data.duration_seconds} 秒计时器。"
        task_ref = TaskRef(
            task_id=runtime.task_id,
            task_type=runtime.task_type,
            state=runtime.state,
            summary=summary,
        )
        return CapabilityResult.success(
            data={
                "task_id": runtime.task_id,
                "task_type": runtime.task_type,
                "state": runtime.state,
                "summary": summary,
            },
            message=summary,
            task_refs=[task_ref],
        )
