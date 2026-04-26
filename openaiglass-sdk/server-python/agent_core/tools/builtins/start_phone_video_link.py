"""启动眼镜与手机视频直连任务 Tool。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_core.context.models import TaskRef
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTool
from agent_core.tools.device_group_resolver import resolve_bound_phone_id, resolve_phone_camera_sink_uri
from infra.errors import ErrorCode, build_error


class StartPhoneVideoLinkInput(BaseModel):
    """启动视频直连任务输入。"""

    phone_device_id: str | None = Field(default=None, description="目标手机设备编号，不传则使用当前绑定手机")
    link_mode: str = Field(default="direct", description="链路模式，首版默认 direct")
    reason: str = Field(default="agent_requested", description="创建任务的原因")
    frame_interval_ms: int = Field(default=500, description="帧发送间隔，单位毫秒")


class StartPhoneVideoLinkOutput(BaseModel):
    """启动视频直连任务输出。"""

    task_id: str
    task_type: str
    state: str
    phone_device_id: str
    summary: str


class StartPhoneVideoLinkTool(BaseTool):
    """基于当前绑定关系创建视频直连后台任务。"""

    spec = ToolSpec(
        name="start_phone_video_link",
        description="当需要创建眼镜与当前绑定手机之间的视频直连后台任务时使用。",
        input_model=StartPhoneVideoLinkInput,
        output_model=StartPhoneVideoLinkOutput,
        capability_type="tool",
        tags=["phone", "video", "task"],
    )

    def run(self, context: AgentToolContext, input_data: StartPhoneVideoLinkInput) -> CapabilityResult:
        """执行视频直连任务创建逻辑。

        主要逻辑：
        1. 从运行态快照读取当前设备绑定关系。
        2. 确认当前眼镜已绑定手机，或校验显式传入手机编号与绑定关系一致。
        3. 调用 `TaskGateway` 创建 `phone_video_link_task`。
        """

        if context.task_gateway is None:
            raise build_error(ErrorCode.INVALID_CONFIG, "TaskGateway 未配置，无法创建视频直连任务")

        runtime_snapshot = context.device_state_reader()
        bound_phone_id = resolve_bound_phone_id(runtime_snapshot=runtime_snapshot, glass_device_id=context.device_id)
        requested_phone_id = (input_data.phone_device_id or "").strip()
        if requested_phone_id and requested_phone_id != bound_phone_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "指定手机与当前绑定关系不一致",
                details={
                    "glass_device_id": context.device_id,
                    "bound_phone_device_id": bound_phone_id,
                    "requested_phone_device_id": requested_phone_id,
                },
            )

        target_ws_uri = resolve_phone_camera_sink_uri(runtime_snapshot=runtime_snapshot, phone_device_id=bound_phone_id)

        runtime = context.task_gateway.create_task(
            task_type="phone_video_link_task",
            session_id=context.session_id,
            device_id=context.device_id,
            input_data={
                "phone_device_id": bound_phone_id,
                "target_ws_uri": target_ws_uri,
                "link_mode": input_data.link_mode,
                "reason": input_data.reason,
                "frame_interval_ms": input_data.frame_interval_ms,
            },
        )
        summary = f"已创建视频直连任务，目标手机是 {bound_phone_id}。"
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
                "phone_device_id": bound_phone_id,
                "target_ws_uri": target_ws_uri,
                "summary": summary,
            },
            message=summary,
            task_refs=[task_ref],
        )
