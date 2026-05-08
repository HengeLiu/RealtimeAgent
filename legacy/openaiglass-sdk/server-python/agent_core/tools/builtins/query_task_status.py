"""查询任务状态 Tool。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_core.context import generate_id
from agent_core.context.models import DerivedArtifact, TaskRef
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTaskTool
from infra.errors import ErrorCode, build_error


class QueryTaskStatusInput(BaseModel):
    """查询任务输入。"""

    task_id: str = Field(
        description="要查询的后台任务编号，通常来自此前工具返回的 task_id；没有明确任务编号时不要猜测。",
    )


class QueryTaskStatusOutput(BaseModel):
    """查询任务输出。"""

    task_id: str
    task_type: str
    state: str
    summary: str


class QueryTaskStatusTool(BaseTaskTool):
    """查询任务当前状态。"""

    spec = ToolSpec(
        name="query_task_status",
        description="当用户询问某个已启动后台任务的进度、状态或结果时调用；只适用于已经有 task_id 的任务。",
        input_model=QueryTaskStatusInput,
        output_model=QueryTaskStatusOutput,
        capability_type="task",
        tags=["task", "status"],
        progress_message=[
            "我先查一下任务状态。",
            "稍等，我看一下任务进度。",
            "我确认一下这个任务现在到哪了。",
        ],
    )

    def run(self, context: AgentToolContext, input_data: QueryTaskStatusInput) -> CapabilityResult:
        if context.task_gateway is None:
            raise build_error(ErrorCode.INVALID_CONFIG, "TaskGateway 未配置，无法查询任务")
        runtime = context.task_gateway.query_task(input_data.task_id)
        summary = f"任务 {runtime.task_id} 当前状态是 {runtime.state}。"
        artifact = DerivedArtifact(
            artifact_id=generate_id("artifact"),
            session_id=context.session_id,
            artifact_type="task_status_snapshot",
            storage_uri=f"memory://task/{runtime.task_id}/status",
            text=summary,
            meta={"task_id": runtime.task_id, "state": runtime.state},
        )
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
            derived_artifacts=[artifact],
            task_refs=[task_ref],
        )
