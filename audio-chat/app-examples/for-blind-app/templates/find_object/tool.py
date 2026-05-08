from __future__ import annotations

from pydantic import BaseModel, Field

from audio_chat import BaseTool, ToolContext, ToolResult, ToolSpec


class FindObjectInput(BaseModel):
    """找物 Tool 输入参数。"""

    object_name: str = Field(default="目标物", description="用户想要查找的物品名称。")
    freshness_seconds: float = Field(default=0, ge=0, description="允许复用缓存图片的最长秒数。")
    timeout_seconds: float = Field(default=2, gt=0, description="等待端侧上传图片资产的超时时间，单位秒。")


class FindObjectOutput(BaseModel):
    """找物 Tool 输出结构。"""

    captured: bool = Field(description="是否收到端侧画面。")
    object_name: str = Field(description="要查找的物品名称。")
    asset_id: str | None = Field(default=None, description="图片资产 ID。")
    stream_type: str | None = Field(default=None, description="资产来源 stream 类型。")
    path: str | None = Field(default=None, description="本地调试路径。")


class FindObjectTool(BaseTool):
    """找物能力迁移样板。

    主要功能：
    1. 请求具备 `sensor.rgb` 能力的端侧上传一张图片资产。
    2. 返回资产引用和待查找目标，后续可以交给模型或独立视觉处理器。
    3. 保持业务代码只使用 `UserDeviceContext`，不指定具体设备。
    """

    spec = ToolSpec(
        name="find_object",
        description="请求端侧采集图片，并准备一次找物分析。",
        input_model=FindObjectInput,
        output_model=FindObjectOutput,
        progress_message="正在请求端侧画面",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行找物 Tool。

        主要逻辑：
        1. 从入参读取目标名称。
        2. 通过 `context.devices.request_asset()` 请求 `sensor.rgb` 资产。
        3. 图片字节由端侧通过 stream 上传，Tool 只读取 `AssetRef`。

        参数：
        1. `context`：SDK 注入的工具上下文。
        2. `input_data`：模型生成的参数，建议包含 `object_name`。

        返回值：
        1. 成功时返回目标名称和图片资产引用。
        2. 超时时返回 `captured=false`，方便 Agent 解释失败。

        异常情况：
        1. 事件发布、stream 或资产缓存异常由 SDK 统一转换为 ToolResult 失败。
        """

        object_name = input_data["object_name"].strip()
        asset = context.devices.request_asset(
            "sensor.rgb",
            freshness_seconds=float(input_data["freshness_seconds"]),
            configure_payload={
                "reason": "find_object",
                "format": "jpeg",
                "object_name": object_name,
            },
            timeout_seconds=float(input_data["timeout_seconds"]),
        )
        if asset is None:
            return ToolResult.success(
                data={"captured": False, "object_name": object_name},
                message="未收到端侧画面，无法开始找物",
            )
        return ToolResult.success(
            data={
                "captured": True,
                "object_name": object_name,
                "asset_id": asset.asset_id,
                "stream_type": asset.stream_type,
                "path": asset.path,
            },
            message=f"已收到画面，可以开始查找{object_name}",
            assets=[asset],
        )
