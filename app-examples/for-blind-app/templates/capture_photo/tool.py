from __future__ import annotations

from pydantic import BaseModel, Field

from audio_chat import BaseTool, ToolContext, ToolResult, ToolSpec


class CapturePhotoInput(BaseModel):
    """抓拍 Tool 输入参数。"""

    reason: str = Field(default="developer_capture_photo", description="请求抓拍的业务原因。")
    freshness_seconds: float = Field(default=0, ge=0, description="允许复用缓存图片的最长秒数，0 表示必须新采集。")
    timeout_seconds: float = Field(default=2, gt=0, description="等待图片返回的超时时间，单位秒。")


class CapturePhotoOutput(BaseModel):
    """抓拍 Tool 输出结构。"""

    captured: bool = Field(description="是否收到图片资产。")
    reason: str | None = Field(default=None, description="失败或请求原因。")
    asset_id: str | None = Field(default=None, description="图片资产 ID。")
    stream_type: str | None = Field(default=None, description="资产来源类型。")
    path: str | None = Field(default=None, description="本地调试路径。")


class CapturePhotoTool(BaseTool):
    """请求端侧上传一张 RGB 图片的示例 Tool。

    主要功能：
    1. 先查询 Asset Service 中的新鲜 `sensor.rgb` 资产。
    2. 缓存未命中时，通过 `UserDeviceContext.request_asset()` 发布控制事件请求端侧上传。
    3. 返回 `AssetRef`，不直接访问端侧连接或硬编码 device_id。
    """

    spec = ToolSpec(
        name="capture_photo",
        description="采集一张当前 RGB 图片并返回资产引用。适合需要看当前画面的简单示例。",
        input_model=CapturePhotoInput,
        output_model=CapturePhotoOutput,
        progress_message=("我先拍张照片。", "稍等，我看一下当前画面。"),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行抓拍请求。

        主要逻辑：只通过 `context.devices.request_asset()` 使用设备通讯能力；端侧实际图片
        数据必须通过 `sensor.rgb` stream 上传，不能放进控制事件 payload。
        参数：`context` 为 SDK 注入的 Tool 上下文，`input_data` 可包含 `reason`。
        返回值：成功时包含图片资产引用，超时时返回可解释结果。
        异常情况：底层事件发布或资产写入异常由 SDK 转换为 ToolResult 失败。
        """

        reason = input_data["reason"]
        asset = context.devices.request_asset(
            "sensor.rgb",
            freshness_seconds=float(input_data["freshness_seconds"]),
            configure_payload={
                "reason": reason,
                "format": "jpeg",
            },
            timeout_seconds=float(input_data["timeout_seconds"]),
        )
        if asset is None:
            return ToolResult.success(
                data={"captured": False, "reason": "asset_timeout"},
                message="未在超时时间内收到图片",
            )
        return ToolResult.success(
            data={
                "captured": True,
                "asset_id": asset.asset_id,
                "stream_type": asset.stream_type,
                "path": asset.path,
            },
            message="已收到图片资产",
            assets=[asset],
        )
