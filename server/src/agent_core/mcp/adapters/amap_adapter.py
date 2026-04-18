"""AMap MCP 适配器。"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field

from agent_core.context.models import DerivedArtifact, generate_id
from agent_core.mcp.base import BaseMcpAdapter
from agent_core.models import CapabilityResult, McpMethodSpec
from infra.errors import ErrorCode, build_error


class AmapPoiSearchInput(BaseModel):
    """POI 搜索输入。"""

    query: str = Field(description="搜索关键词")
    city: str | None = Field(default=None, description="可选城市")


class AmapGeocodeInput(BaseModel):
    """地理编码输入。"""

    address: str = Field(description="待编码地址")
    city: str | None = Field(default=None, description="可选城市")


class AmapRoutePlanInput(BaseModel):
    """路线规划输入。"""

    origin: str = Field(description="起点")
    destination: str = Field(description="终点")
    strategy: str = Field(default="walking", description="路线策略，当前默认 walking")


class AmapMcpAdapter(BaseMcpAdapter):
    """Phase E 使用的 AMap Mock Adapter。"""

    adapter_name = "amap"

    def __init__(self, *, mock_mode: bool = True) -> None:
        self._mock_mode = mock_mode

    def list_methods(self) -> list[McpMethodSpec]:
        return [
            McpMethodSpec(
                name="amap.poi_search",
                description="搜索目的地或周边兴趣点",
                input_model=AmapPoiSearchInput,
                tags=["amap", "poi"],
            ),
            McpMethodSpec(
                name="amap.geocode",
                description="把地址转换成坐标",
                input_model=AmapGeocodeInput,
                tags=["amap", "geocode"],
            ),
            McpMethodSpec(
                name="amap.route_plan",
                description="规划从起点到终点的路线",
                input_model=AmapRoutePlanInput,
                tags=["amap", "route"],
            ),
        ]

    def invoke(self, *, method_name: str, context, input_data) -> CapabilityResult:
        if not self._mock_mode:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "当前仓库尚未接入真实 AMap 环境，请先使用 mock_mode",
                details={"method_name": method_name},
            )
        if method_name == "amap.poi_search":
            return self._poi_search(context.session_id, input_data.query, input_data.city)
        if method_name == "amap.geocode":
            return self._geocode(context.session_id, input_data.address, input_data.city)
        if method_name == "amap.route_plan":
            return self._route_plan(context.session_id, input_data.origin, input_data.destination, input_data.strategy)
        raise build_error(
            ErrorCode.TASK_NOT_FOUND,
            "未支持的 AMap MCP 方法",
            details={"method_name": method_name},
        )

    def _poi_search(self, session_id: str, query: str, city: str | None) -> CapabilityResult:
        pois = [
            {
                "poi_id": f"poi_{self._stable_hash(query + str(city))[:8]}",
                "name": f"{query}一店",
                "city": city or "上海",
                "address": f"{city or '上海'}示例路 18 号",
            },
            {
                "poi_id": f"poi_{self._stable_hash('alt' + query + str(city))[:8]}",
                "name": f"{query}二店",
                "city": city or "上海",
                "address": f"{city or '上海'}示例路 66 号",
            },
        ]
        artifact = DerivedArtifact(
            artifact_id=generate_id("artifact"),
            session_id=session_id,
            artifact_type="amap_poi_search",
            storage_uri=f"memory://amap/poi/{query}",
            text=f"搜索到 {len(pois)} 个与 {query} 相关的地点",
            meta={"query": query, "city": city, "pois": pois},
        )
        return CapabilityResult.success(
            data={"query": query, "city": city or "上海", "pois": pois},
            message=artifact.text,
            derived_artifacts=[artifact],
        )

    def _geocode(self, session_id: str, address: str, city: str | None) -> CapabilityResult:
        location = self._fake_coordinate(address, city)
        artifact = DerivedArtifact(
            artifact_id=generate_id("artifact"),
            session_id=session_id,
            artifact_type="amap_geocode",
            storage_uri=f"memory://amap/geocode/{address}",
            text=f"{address} 的模拟坐标为 {location}",
            meta={"address": address, "city": city, "location": location},
        )
        return CapabilityResult.success(
            data={"address": address, "city": city, "location": location},
            message=artifact.text,
            derived_artifacts=[artifact],
        )

    def _route_plan(self, session_id: str, origin: str, destination: str, strategy: str) -> CapabilityResult:
        if not origin.strip() or not destination.strip():
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "路线规划需要同时提供起点和终点",
                details={"origin": origin, "destination": destination},
            )
        origin_location = self._fake_coordinate(origin)
        destination_location = self._fake_coordinate(destination)
        distance_m = 1280
        duration_s = 960
        summary = f"从{origin}到{destination}约 {distance_m} 米，步行约 {duration_s // 60} 分钟。"
        artifact = DerivedArtifact(
            artifact_id=generate_id("artifact"),
            session_id=session_id,
            artifact_type="amap_route_plan",
            storage_uri=f"memory://amap/route/{origin}->{destination}",
            text=summary,
            meta={
                "origin": origin,
                "origin_location": origin_location,
                "destination": destination,
                "destination_location": destination_location,
                "strategy": strategy,
                "distance_m": distance_m,
                "duration_s": duration_s,
            },
        )
        return CapabilityResult.success(
            data={
                "origin": origin,
                "origin_location": origin_location,
                "destination": destination,
                "destination_location": destination_location,
                "strategy": strategy,
                "distance_m": distance_m,
                "duration_s": duration_s,
                "summary": summary,
            },
            message=summary,
            derived_artifacts=[artifact],
        )

    @staticmethod
    def _stable_hash(text: str) -> str:
        return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()

    def _fake_coordinate(self, main: str, city: str | None = None) -> str:
        digest = self._stable_hash(f"{city or ''}:{main}")
        lat = 31.2000 + (int(digest[:4], 16) % 5000) / 100000
        lon = 121.4000 + (int(digest[4:8], 16) % 5000) / 100000
        return f"{lon:.6f},{lat:.6f}"
