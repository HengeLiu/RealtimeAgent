from __future__ import annotations

from pydantic import BaseModel, Field

from audio_chat import BaseTool, ToolContext, ToolResult, ToolSpec


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

    主要功能：
    1. 调用 MCP mock 路线规划工具。
    2. 返回路线摘要，供 Agent 用于回答或后续启动导航 Task。
    3. MCP 不直接持有设备上下文，不启动后台任务。
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
