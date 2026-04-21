"""地图管理 Tool。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTool
from infra.errors import ErrorCode, build_error


class MapManageInput(BaseModel):
    """地图管理输入。

    主要功能：
    1. 统一封装地点搜索、地址解析和路线规划三类地图需求。
    2. 让模型只面对一个稳定高层入口，而不是直接操作底层地图方法。
    """

    action: Literal["auto", "search", "geocode", "route"] = Field(
        default="auto",
        description="地图动作，默认由输入字段自动判断",
    )
    query: str | None = Field(default=None, description="地点搜索关键词")
    city: str | None = Field(default=None, description="搜索或编码时可选城市")
    address: str | None = Field(default=None, description="待解析地址")
    origin: str | None = Field(default=None, description="路线起点")
    destination: str | None = Field(default=None, description="路线终点")
    strategy: str = Field(default="walking", description="路线策略，默认步行")


class MapManageOutput(BaseModel):
    """地图管理输出。"""

    summary: str
    action: Literal["search", "geocode", "route"]
    query: str | None = None
    city: str | None = None
    address: str | None = None
    location: str | None = None
    origin: str | None = None
    destination: str | None = None
    strategy: str | None = None
    pois: list[dict[str, str]] | None = None
    distance_m: int | None = None
    duration_s: int | None = None


class MapManageTool(BaseTool):
    """统一封装地图相关复合流程。

    主要功能：
    1. 对模型暴露高层入口 `map_manage`。
    2. 在内部继续通过 `McpGateway` 调用 `amap.*` 底层能力。
    """

    spec = ToolSpec(
        name="map_manage",
        description=(
            "当用户需要搜索地点、确认地址位置或规划路线时使用。"
            "如果用户已经给出起点和终点，就规划路线；如果只是找地点，就先搜索地点。"
        ),
        input_model=MapManageInput,
        output_model=MapManageOutput,
        capability_type="tool",
        tags=["map", "navigation", "amap"],
    )

    def run(self, context: AgentToolContext, input_data: MapManageInput) -> CapabilityResult:
        """执行地图复合流程。

        主要逻辑：
        1. 先根据显式动作或字段内容确定当前属于搜索、解析还是路线规划。
        2. 再调用底层 `amap.*` MCP 方法。
        3. 最后把底层结果整理成适合直接播报的高层摘要。

        参数：
        1. `context`：能力调用上下文。
        2. `input_data`：地图管理输入。

        返回值：
        1. 统一 `CapabilityResult`，包含摘要和底层结果要点。

        异常情况：
        1. 未配置 `McpGateway` 时抛出配置错误。
        2. 必填字段缺失时抛出消息错误。
        """

        if context.mcp_gateway is None:
            raise build_error(ErrorCode.INVALID_CONFIG, "McpGateway 未配置，无法处理地图请求")

        action = self._resolve_action(input_data)
        if action == "route":
            if not input_data.origin or not input_data.destination:
                raise build_error(ErrorCode.INVALID_MESSAGE, "路线规划需要同时提供起点和终点")
            result = context.mcp_gateway.invoke(
                name="amap.route_plan",
                context=context,
                arguments={
                    "origin": input_data.origin,
                    "destination": input_data.destination,
                    "strategy": input_data.strategy,
                },
            )
            return CapabilityResult.success(
                data={
                    "action": "route",
                    "summary": result.data["summary"],
                    "origin": result.data["origin"],
                    "destination": result.data["destination"],
                    "strategy": result.data["strategy"],
                    "distance_m": result.data["distance_m"],
                    "duration_s": result.data["duration_s"],
                },
                message=result.message,
                asset_refs=result.asset_refs,
                derived_artifacts=result.derived_artifacts,
                task_refs=result.task_refs,
            )

        if action == "geocode":
            if not input_data.address:
                raise build_error(ErrorCode.INVALID_MESSAGE, "地址解析需要 address")
            result = context.mcp_gateway.invoke(
                name="amap.geocode",
                context=context,
                arguments={
                    "address": input_data.address,
                    "city": input_data.city,
                },
            )
            summary = f"{result.data['address']} 的位置坐标是 {result.data['location']}。"
            return CapabilityResult.success(
                data={
                    "action": "geocode",
                    "summary": summary,
                    "address": result.data["address"],
                    "city": result.data["city"],
                    "location": result.data["location"],
                },
                message=summary,
                asset_refs=result.asset_refs,
                derived_artifacts=result.derived_artifacts,
                task_refs=result.task_refs,
            )

        if not input_data.query:
            raise build_error(ErrorCode.INVALID_MESSAGE, "地点搜索需要 query")
        result = context.mcp_gateway.invoke(
            name="amap.poi_search",
            context=context,
            arguments={
                "query": input_data.query,
                "city": input_data.city,
            },
        )
        pois = result.data["pois"]
        top_name = pois[0]["name"] if pois else input_data.query
        summary = f"我先帮你找到 {top_name} 这类地点，共 {len(pois)} 个结果。"
        return CapabilityResult.success(
            data={
                "action": "search",
                "summary": summary,
                "query": result.data["query"],
                "city": result.data["city"],
                "pois": pois,
            },
            message=summary,
            asset_refs=result.asset_refs,
            derived_artifacts=result.derived_artifacts,
            task_refs=result.task_refs,
        )

    @staticmethod
    def _resolve_action(input_data: MapManageInput) -> Literal["search", "geocode", "route"]:
        """根据输入字段推断地图动作。"""

        if input_data.action != "auto":
            return input_data.action
        if input_data.origin or input_data.destination:
            return "route"
        if input_data.address:
            return "geocode"
        return "search"
