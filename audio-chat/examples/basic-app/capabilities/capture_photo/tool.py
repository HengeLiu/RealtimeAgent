from __future__ import annotations

from audio_chat import BaseTool, ToolContext, ToolResult


class CapturePhotoTool(BaseTool):
    """请求端侧上传一张 RGB 图片的示例 Tool。

    主要功能：
    1. 先查询 Asset Service 中的新鲜 `sensor.rgb` 资产。
    2. 缓存未命中时，通过 `UserDeviceContext.request_asset()` 发布控制事件请求端侧上传。
    3. 返回 `AssetRef`，不直接访问端侧连接或硬编码 device_id。
    """

    name = "capture_photo"
    description = "请求端侧采集一张 RGB 图片并返回资产引用"
    progress_message = "正在请求端侧拍照"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行抓拍请求。

        主要逻辑：只通过 `context.devices.request_asset()` 使用设备能力；端侧实际图片
        数据必须通过 `sensor.rgb` stream 上传，不能放进控制事件 payload。
        参数：`context` 为 SDK 注入的 Tool 上下文，`input_data` 可包含 `reason`。
        返回值：成功时包含图片资产引用，超时时返回可解释结果。
        异常情况：底层事件发布或资产写入异常由 SDK 转换为 ToolResult 失败。
        """

        reason = str(input_data.get("reason") or "developer_capture_photo")
        asset = context.devices.request_asset(
            "sensor.rgb",
            freshness_seconds=float(input_data.get("freshness_seconds") or 0),
            configure_payload={
                "reason": reason,
                "format": "jpeg",
            },
            timeout_seconds=float(input_data.get("timeout_seconds") or 2),
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
            message="已收到端侧图片资产",
            assets=[asset],
        )
