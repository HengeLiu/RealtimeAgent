"""业务侧 AMap MCP mock adapter。"""

from __future__ import annotations

from typing import Any

from agent_core.mcp import BaseMcpAdapter
from agent_core.models import CapabilityResult, McpMethodSpec
from pydantic import BaseModel, Field


class AmapRoutePlanInput(BaseModel):
    """AMap 路线规划输入。"""

    origin: str = Field(default="", description="路线起点名称或坐标；不知道时可以留空表示当前位置。")
    destination: str = Field(description="路线终点名称或坐标，应使用已确认的目的地。")
    strategy: str = Field(default="walking", description="路线策略；盲人步行导航通常使用 walking。")


class AmapPoiSearchInput(BaseModel):
    """AMap POI 搜索输入。"""

    keyword: str = Field(description="用户想去的地点关键词，例如商场、医院、地铁站或具体店名。")
    city: str = Field(default="", description="限定搜索的城市；用户没有说明城市时可以留空。")


class AmapGeocodeInput(BaseModel):
    """AMap 地理编码输入。"""

    poi_id: str = Field(default="", description="已确认目的地候选的 POI 编号；没有编号时可以留空。")
    address: str = Field(default="", description="要解析坐标的地址或地点名称；没有 POI 编号时必须填写。")


class MockAmapMcpAdapter(BaseMcpAdapter):
    """AMap MCP mock adapter。

    主要功能：
    1. 在业务工程内提供可回放的 `amap.poi_search`、`amap.geocode` 和 `amap.route_plan` 方法。
    2. 验证业务 Tool 通过 SDK `context.mcp(...)` 入口调用 MCP。
    3. 为真实 AMap adapter 接入前提供稳定测试替身。

    主要方法：
    1. `list_methods`：声明可调用 MCP 方法。
    2. `invoke`：返回结构化路线规划结果。
    """

    adapter_name = "blind_navigation_mock_amap"

    def list_methods(self) -> list[McpMethodSpec]:
        """列出当前 adapter 支持的 MCP 方法。

        参数：
        1. 无。

        返回值：
        1. `McpMethodSpec` 列表。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        return [
            McpMethodSpec(
                name="amap.poi_search",
                description="当目的地可能有多个候选时搜索可选地点，用于让用户确认具体目的地。",
                input_model=AmapPoiSearchInput,
                tags=["navigation", "amap", "mock"],
            ),
            McpMethodSpec(
                name="amap.geocode",
                description="把用户确认的目的地候选或地址解析成可用于路线规划的位置。",
                input_model=AmapGeocodeInput,
                tags=["navigation", "amap", "mock"],
            ),
            McpMethodSpec(
                name="amap.route_plan",
                description="在起点和已确认目的地之间规划步行路线。",
                input_model=AmapRoutePlanInput,
                tags=["navigation", "amap", "mock"],
            ),
        ]

    def invoke(self, *, method_name: str, context, input_data) -> CapabilityResult:
        """执行 mock AMap MCP 调用。

        参数：
        1. `method_name`：MCP 方法名。
        2. `context`：SDK MCP 调用上下文。
        3. `input_data`：已通过 pydantic 校验的输入对象。

        返回值：
        1. `CapabilityResult`：成功时包含路线摘要、距离、耗时和步骤。

        异常情况：
        1. 方法名不支持或目的地为空时返回结构化失败结果。
        """

        if method_name == "amap.poi_search":
            return self._invoke_poi_search(input_data)
        if method_name == "amap.geocode":
            return self._invoke_geocode(input_data)
        if method_name == "amap.route_plan":
            return self._invoke_route_plan(input_data)
        return CapabilityResult.failed(
            code="unsupported_mcp_method",
            message=f"不支持的 AMap MCP 方法: {method_name}",
            details={"method_name": method_name},
        )

    def _invoke_poi_search(self, input_data) -> CapabilityResult:
        """执行 mock POI 搜索。"""

        keyword = str(input_data.keyword or "").strip()
        if not keyword:
            return CapabilityResult.failed(
                code="invalid_input",
                message="keyword 不能为空",
                details={"field": "keyword"},
            )
        city = str(input_data.city or "").strip()
        candidates = self._build_mock_candidates(keyword=keyword, city=city)
        return CapabilityResult.success(
            data={
                "keyword": keyword,
                "city": city,
                "candidates": candidates,
                "candidate_count": len(candidates),
            },
            message=f"找到 {len(candidates)} 个目的地候选",
            meta={"adapter": self.adapter_name, "mock": True},
        )

    def _invoke_geocode(self, input_data) -> CapabilityResult:
        """执行 mock 地理编码。"""

        poi_id = str(input_data.poi_id or "").strip()
        address = str(input_data.address or "").strip()
        if not poi_id and not address:
            return CapabilityResult.failed(
                code="invalid_input",
                message="poi_id 和 address 不能同时为空",
                details={"fields": ["poi_id", "address"]},
            )
        resolved = self._resolve_mock_location(poi_id=poi_id, address=address)
        return CapabilityResult.success(
            data=resolved,
            message=f"已解析目的地坐标：{resolved['name']}",
            meta={"adapter": self.adapter_name, "mock": True},
        )

    def _invoke_route_plan(self, input_data) -> CapabilityResult:
        """执行 mock 路线规划。"""

        destination = str(input_data.destination or "").strip()
        if not destination:
            return CapabilityResult.failed(
                code="invalid_input",
                message="destination 不能为空",
                details={"field": "destination"},
            )

        origin = str(input_data.origin or "当前位置").strip() or "当前位置"
        strategy = str(input_data.strategy or "walking").strip() or "walking"
        route = self._build_mock_route(origin=origin, destination=destination, strategy=strategy)
        return CapabilityResult.success(
            data=route,
            message=f"已规划从{origin}到{destination}的路线",
            meta={"adapter": self.adapter_name, "mock": True},
        )

    @staticmethod
    def _build_mock_candidates(*, keyword: str, city: str) -> list[dict[str, Any]]:
        """生成稳定的 mock POI 候选。"""

        normalized_city = city or "当前城市"
        if "桂林路" in keyword:
            return [
                {
                    "poi_id": "poi_guilin_road_station",
                    "name": "桂林路地铁站",
                    "address": f"{normalized_city} 桂林路站 1 号口",
                    "city": normalized_city,
                    "location": "121.418000,31.175000",
                    "confidence": 0.96,
                },
                {
                    "poi_id": "poi_guilin_road_bus",
                    "name": "桂林路公交站",
                    "address": f"{normalized_city} 桂林路公交站",
                    "city": normalized_city,
                    "location": "121.419100,31.174300",
                    "confidence": 0.72,
                },
            ]
        return [
            {
                "poi_id": f"poi_{sum(ord(char) for char in keyword) % 100000}",
                "name": keyword,
                "address": f"{normalized_city} {keyword}",
                "city": normalized_city,
                "location": "121.400000,31.170000",
                "confidence": 0.86,
            }
        ]

    @classmethod
    def _resolve_mock_location(cls, *, poi_id: str, address: str) -> dict[str, Any]:
        """根据 POI 编号或地址返回稳定位置。"""

        candidates = cls._build_mock_candidates(keyword=address or poi_id, city="")
        for candidate in candidates:
            if poi_id and candidate["poi_id"] == poi_id:
                return dict(candidate)
        if poi_id == "poi_guilin_road_station":
            return dict(cls._build_mock_candidates(keyword="桂林路地铁站", city="")[0])
        first = dict(candidates[0])
        if poi_id:
            first["poi_id"] = poi_id
        return first

    @staticmethod
    def _build_mock_route(*, origin: str, destination: str, strategy: str) -> dict[str, Any]:
        """生成稳定的 mock 路线。

        参数：
        1. `origin`：路线起点。
        2. `destination`：路线终点。
        3. `strategy`：路线策略。

        返回值：
        1. 路线结构化字典。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        distance_meters = max(320, min(2400, len(origin + destination) * 95))
        duration_minutes = max(4, round(distance_meters / 75))
        return {
            "provider": "amap_mock",
            "origin": origin,
            "destination": destination,
            "strategy": strategy,
            "distance_meters": distance_meters,
            "duration_minutes": duration_minutes,
            "summary": f"从{origin}步行到{destination}，约{distance_meters}米，预计{duration_minutes}分钟",
            "steps": [
                {"instruction": f"从{origin}出发，沿当前道路直行", "distance_meters": distance_meters // 3},
                {"instruction": "到达路口后等待红绿灯并确认安全", "distance_meters": distance_meters // 6},
                {"instruction": f"继续前进到达{destination}", "distance_meters": distance_meters - distance_meters // 3 - distance_meters // 6},
            ],
        }
