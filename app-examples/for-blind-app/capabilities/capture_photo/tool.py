from __future__ import annotations

from pydantic import BaseModel, Field

from audio_chat import BaseTool, ToolContext, ToolResult, ToolSpec


class CapturePhotoInput(BaseModel):
    """抓拍 Tool 输入参数。"""

    reason: str = Field(default="agent_requested", description="请求抓拍的业务原因。")
    timeout_seconds: float = Field(default=5, gt=0, description="等待图片返回的超时时间，单位秒。")


class CapturePhotoOutput(BaseModel):
    """抓拍 Tool 输出结构。"""

    captured: bool = Field(description="是否收到图片资产。")
    asset_id: str | None = Field(default=None, description="图片资产 ID。")
    stream_type: str | None = Field(default=None, description="资产来源类型。")
    uri: str | None = Field(default=None, description="资产 URI。")
    mime_type: str | None = Field(default=None, description="资产 MIME 类型。")


class CapturePhotoTool(BaseTool):
    """for-blind-app 当前画面抓拍 Tool。

    主要功能：通过 `context.devices.sensors.rgb.one()` 获取一张 RGB 图片资产。
    该工具属于 App 业务能力，不是 SDK 内置 Tool。
    """

    spec = ToolSpec(
        name="capture_photo",
        description="当用户需要了解当前画面、障碍物、文字或路况时，采集一张当前 RGB 图片。",
        input_model=CapturePhotoInput,
        output_model=CapturePhotoOutput,
        progress_message=("我先拍张照片看看。", "稍等，我看一下当前画面。"),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行当前画面抓拍。

        主要逻辑：只使用 Context 设备 API 请求 `sensor.rgb` 单帧资产；图片字节
        由端侧通过 stream 上传，Tool 只返回资产引用。
        参数：`context` 为 SDK 注入上下文，`input_data` 包含 reason 和 timeout_seconds。
        返回值：成功时返回 `AssetRef`，失败由 ToolGateway 转换为结构化错误。
        异常情况：设备不可用或超时时由底层 Context API 抛出。
        """

        asset = await context.devices.sensors.rgb.one(
            params={"reason": str(input_data.get("reason") or "agent_requested"), "format": "jpeg"},
            timeout_seconds=float(input_data.get("timeout_seconds") or 5),
        )
        return ToolResult.success(
            data={
                "captured": True,
                "asset_id": asset.asset_id,
                "stream_type": asset.stream_type,
                "uri": asset.uri,
                "mime_type": asset.mime_type,
            },
            assets=[asset],
            message="已获取当前画面。",
        )
