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

    phone_device_id: str | None = Field(
        default=None,
        description="目标手机设备编号；通常留空，表示使用当前眼镜已绑定的手机。",
    )
    link_mode: str = Field(
        default="direct",
        description="视频连接方式；一般使用默认值 direct，除非用户或上层流程明确要求其他方式。",
    )
    reason: str = Field(
        default="agent_requested",
        description="说明为什么需要启动手机视频直连，例如“持续观察前方路况”或“辅助找物”。",
    )
    frame_interval_ms: int = Field(
        default=500,
        description="手机向服务端发送视频帧的间隔，单位毫秒；数值越小越实时但资源消耗越高。",
    )


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
        description=(
            "当任务需要手机摄像头持续回传画面时调用，例如持续观察、找物、导航或红绿灯辅助。"
            "只需要单张眼前照片时不要调用，应使用 capture_photo。"
        ),
        input_model=StartPhoneVideoLinkInput,
        output_model=StartPhoneVideoLinkOutput,
        capability_type="tool",
        tags=["phone", "video", "task"],
        progress_message=[
            "我先连接手机摄像头。",
            "稍等，我把手机画面接进来。",
            "我先建立手机和眼镜的视频连接。",
        ],
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
