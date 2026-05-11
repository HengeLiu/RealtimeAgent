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
    """当前画面抓拍 Tool。

    主要功能：通过 `context.devices.sensors.rgb.one()` 获取一张 RGB 图片资产。
    该工具属于 for-blind-app 业务能力，不是 SDK 内置 Tool。
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
        返回值：成功时返回 `AssetRef`。
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


class QueryRoutePlanInput(BaseModel):
    """路线规划查询 Tool 输入参数。"""

    destination: str = Field(default="盲人服务中心", description="用户想去的目的地名称或地址。")
    origin: str = Field(default="当前位置", description="导航起点；通常使用当前位置。")
    timeout_seconds: float = Field(default=5, gt=0, description="等待路线结果的超时时间，单位秒。")


class QueryRoutePlanOutput(BaseModel):
    """路线规划查询 Tool 输出结构。"""

    route_ready: bool = Field(description="是否准备好可用路线。")
    provider: str | None = Field(default=None, description="路线来源。")
    destination: str | None = Field(default=None, description="导航目的地。")
    route: dict | None = Field(default=None, description="路线结构化结果。")
    error: str | None = Field(default=None, description="fallback 错误说明。")


class QueryRoutePlanTool(BaseTool):
    """路线规划查询 Tool。

    主要功能：优先调用 MCP 路线规划工具；没有 MCP 时返回明确 fallback，不启动后台导航任务。
    """

    spec = ToolSpec(
        name="query_route_plan",
        description="当用户想去某个地点、询问怎么走或需要路线时调用。目的地不明确时，先向用户确认。",
        input_model=QueryRoutePlanInput,
        output_model=QueryRoutePlanOutput,
        progress_message=("我先规划一下路线。", "稍等，我查一下怎么走。"),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行路线规划查询。

        主要逻辑：优先调用 `amap.route_plan` MCP mock；不可用时返回明确 fallback。
        参数：`input_data` 可包含 `destination`、`origin`。
        返回值：路线规划摘要。
        异常情况：MCP 未启用或失败时返回 fallback，不伪装真实地图成功。
        """

        destination = input_data["destination"]
        origin = input_data["origin"]
        mcp = getattr(context, "mcp", None)
        if mcp is None:
            return ToolResult.success(
                data={"provider": "fallback", "destination": destination, "route_ready": False},
                message="路线服务未配置，无法规划真实路线",
            )
        try:
            route = mcp.call(
                tool_name="amap.route_plan",
                arguments={"origin": origin, "destination": destination},
                timeout_seconds=float(input_data["timeout_seconds"]),
            )
        except Exception as exc:
            return ToolResult.success(
                data={"provider": "fallback", "destination": destination, "route_ready": False, "error": str(exc)},
                message="路线规划进入 fallback",
            )
        return ToolResult.success(data={"route_ready": True, "route": route}, message="路线已准备")


class SearchWebInput(BaseModel):
    """搜索 Tool 输入参数。"""

    query: str = Field(default="盲人导航安全提示", description="要搜索的问题或关键词。")
    limit: int = Field(default=3, ge=1, le=10, description="最多返回的搜索结果数量。")
    timeout_seconds: float = Field(default=5, gt=0, description="等待搜索结果的超时时间，单位秒。")


class SearchWebOutput(BaseModel):
    """搜索 Tool 输出结构。"""

    provider: str | None = Field(default=None, description="搜索结果来源。")
    fallback: bool | None = Field(default=None, description="是否使用 fallback。")
    query: str = Field(description="实际搜索词。")
    items: list[dict] | None = Field(default=None, description="搜索结果列表。")
    search: dict | None = Field(default=None, description="搜索返回的原始结构化结果。")
    error: str | None = Field(default=None, description="fallback 错误说明。")


class SearchWebTool(BaseTool):
    """搜索 MCP wrapper Tool。"""

    spec = ToolSpec(
        name="search_web",
        description="当用户明确要求搜索、查询资料、查最新公开信息，或问题需要外部资料时调用。",
        input_model=SearchWebInput,
        output_model=SearchWebOutput,
        progress_message=("我查一下资料。", "稍等，我搜索一下。"),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行搜索。

        主要逻辑：调用 `web.search` MCP mock；没有真实 key 时返回明确 fallback 来源。
        参数：`input_data` 包含 `query`。
        返回值：搜索摘要和引用列表。
        异常情况：MCP 不可用时返回 fallback 结果，不把大正文塞进控制事件。
        """

        query = input_data["query"]
        mcp = getattr(context, "mcp", None)
        if mcp is None:
            return ToolResult.success(
                data={"provider": "fallback", "fallback": True, "query": query, "items": []},
                message="搜索服务未配置，暂时没有搜索结果",
            )
        try:
            result = mcp.call(
                tool_name="web.search",
                arguments={"query": query, "limit": int(input_data["limit"])},
                timeout_seconds=float(input_data["timeout_seconds"]),
            )
        except Exception as exc:
            return ToolResult.success(
                data={"provider": "fallback", "fallback": True, "query": query, "error": str(exc), "items": []},
                message="搜索服务暂时不可用",
            )
        return ToolResult.success(data={"query": query, "search": result}, message="搜索完成")
