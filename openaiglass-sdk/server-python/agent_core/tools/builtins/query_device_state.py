"""查询设备状态 Tool。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTool
from infra.errors import ErrorCode, build_error


class QueryDeviceStateInput(BaseModel):
    """查询设备状态输入。"""

    target_device_id: str | None = Field(
        default=None,
        description="要查询状态的设备编号；不填写时查询当前用户正在使用的眼镜设备。",
    )


class QueryDeviceStateOutput(BaseModel):
    """查询设备状态输出。"""

    device_id: str
    online: bool
    state: str
    session_id: str | None = None
    audio_connection_online: bool = False
    reply_stream_id: str | None = None


class QueryDeviceStateTool(BaseTool):
    """查询设备当前运行状态。"""

    spec = ToolSpec(
        name="query_device_state",
        description="当需要确认设备是否在线、语音连接是否可用、当前是否正在播放回复等运行状态时调用。",
        input_model=QueryDeviceStateInput,
        output_model=QueryDeviceStateOutput,
        capability_type="tool",
        tags=["device", "status"],
        progress_message="我先看一下设备状态。",
    )

    def run(self, context: AgentToolContext, input_data: QueryDeviceStateInput) -> CapabilityResult:
        device_id = (input_data.target_device_id or context.device_id).strip()
        snapshot = context.device_state_reader()
        device_snapshot = self._normalize_device_snapshot(snapshot, device_id)
        if device_snapshot is None:
            raise build_error(
                ErrorCode.STREAM_NOT_FOUND,
                "目标设备当前不在线或状态未知",
                details={"device_id": device_id},
            )
        return CapabilityResult.success(
            data={
                "device_id": device_id,
                "online": True,
                "state": str(device_snapshot.get("state", "unknown")),
                "session_id": device_snapshot.get("session_id"),
                "audio_connection_online": bool(device_snapshot.get("audio_connection_online", False)),
                "reply_stream_id": device_snapshot.get("reply_stream_id"),
            },
        )

    @staticmethod
    def _normalize_device_snapshot(snapshot: dict, device_id: str) -> dict | None:
        if "voice_sessions" in snapshot and isinstance(snapshot["voice_sessions"], dict):
            session_snapshot = snapshot["voice_sessions"].get(device_id)
            return session_snapshot if isinstance(session_snapshot, dict) else None
        candidate = snapshot.get(device_id)
        return candidate if isinstance(candidate, dict) else None
