from __future__ import annotations

from realtime_agent.tools import BaseTool, ToolContext, ToolResult


class CapturePhotoTool(BaseTool):
    """测试用抓拍 Tool。"""

    name = "capture_photo"
    description = "请求一帧 sensor.rgb"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """测试目标：验证 Tool 只通过 ToolDeviceFacade 请求资产。"""

        asset = await context.devices.sensors.rgb.one(
            params={},
            timeout_seconds=1,
        )
        if asset is None:
            return ToolResult.success(data={"captured": False}, message="asset timeout")
        return ToolResult.success(data={"captured": True, "asset_id": asset.asset_id}, assets=[asset])
