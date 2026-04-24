"""启动找物体任务 Tool。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_core.context.models import TaskRef
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTool
from infra.errors import ErrorCode, build_error


class StartFindObjectInput(BaseModel):
    """启动找物体任务输入。"""

    target_object: str = Field(description="需要寻找的目标物体名称")
    frame_interval_ms: int = Field(default=500, description="视频帧发送间隔，单位毫秒")
    reason: str = Field(default="agent_requested", description="创建任务的原因")


class StartFindObjectOutput(BaseModel):
    """启动找物体任务输出。"""

    task_id: str
    task_type: str
    state: str
    target_object: str
    phone_device_id: str
    summary: str


class StartFindObjectTool(BaseTool):
    """基于绑定手机创建手机端找物体后台任务。"""

    spec = ToolSpec(
        name="start_find_object",
        description="当用户要求寻找某个眼前物体时使用。该工具会让眼镜把视频发给绑定手机，并由手机端视觉能力寻找目标。",
        input_model=StartFindObjectInput,
        output_model=StartFindObjectOutput,
        capability_type="tool",
        tags=["phone", "vision", "find_object", "task"],
    )

    def run(self, context: AgentToolContext, input_data: StartFindObjectInput) -> CapabilityResult:
        """创建找物体任务。

        主要逻辑：
        1. 从运行态快照读取当前眼镜绑定的手机。
        2. 解析手机上报的视频接收地址。
        3. 创建 `find_object_task`，由后台任务中心管理后续生命周期。

        异常情况：
        1. 未绑定手机、手机未上报接收地址或任务网关缺失时抛出结构化错误。
        """

        if context.task_gateway is None:
            raise build_error(ErrorCode.INVALID_CONFIG, "TaskGateway 未配置，无法创建找物体任务")

        target_object = input_data.target_object.strip()
        if not target_object:
            raise build_error(ErrorCode.INVALID_MESSAGE, "找物体任务需要 target_object")

        runtime_snapshot = context.device_state_reader()
        phone_device_id = self._resolve_bound_phone_id(runtime_snapshot=runtime_snapshot, glass_device_id=context.device_id)
        target_ws_uri = self._resolve_target_ws_uri(runtime_snapshot=runtime_snapshot, phone_device_id=phone_device_id)

        runtime = context.task_gateway.create_task(
            task_type="find_object_task",
            session_id=context.session_id,
            device_id=context.device_id,
            input_data={
                "target_object": target_object,
                "phone_device_id": phone_device_id,
                "target_ws_uri": target_ws_uri,
                "frame_interval_ms": input_data.frame_interval_ms,
                "reason": input_data.reason,
            },
        )
        summary = f"已开始寻找{target_object}，我会在找到后提醒你。"
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
                "target_object": target_object,
                "phone_device_id": phone_device_id,
                "target_ws_uri": target_ws_uri,
                "summary": summary,
            },
            message=summary,
            task_refs=[task_ref],
        )

    @staticmethod
    def _resolve_bound_phone_id(*, runtime_snapshot: dict, glass_device_id: str) -> str:
        """从运行态快照中解析当前眼镜绑定的手机。"""

        bindings = runtime_snapshot.get("device_bindings")
        if not isinstance(bindings, dict):
            raise build_error(ErrorCode.INVALID_CONFIG, "当前运行态缺少设备绑定信息")
        glass_to_phone = bindings.get("glass_to_phone")
        if not isinstance(glass_to_phone, dict):
            raise build_error(ErrorCode.INVALID_CONFIG, "当前运行态缺少 glass_to_phone 绑定信息")
        phone_device_id = str(glass_to_phone.get(glass_device_id) or "").strip()
        if not phone_device_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "当前眼镜尚未绑定手机，无法创建找物体任务",
                details={"glass_device_id": glass_device_id},
            )
        return phone_device_id

    @staticmethod
    def _resolve_target_ws_uri(*, runtime_snapshot: dict, phone_device_id: str) -> str:
        """从运行态快照中解析目标手机的视频接收地址。"""

        connections = runtime_snapshot.get("connections")
        if not isinstance(connections, list):
            raise build_error(ErrorCode.INVALID_CONFIG, "当前运行态缺少连接列表，无法解析手机视频接收地址")
        for connection in connections:
            if not isinstance(connection, dict) or connection.get("device_id") != phone_device_id:
                continue
            camera_sink_ws_uri = str(connection.get("camera_sink_ws_uri") or "").strip()
            if camera_sink_ws_uri:
                return camera_sink_ws_uri
            break
        raise build_error(
            ErrorCode.INVALID_MESSAGE,
            "目标手机尚未上报视频接收地址，无法创建找物体任务",
            details={"phone_device_id": phone_device_id},
        )
