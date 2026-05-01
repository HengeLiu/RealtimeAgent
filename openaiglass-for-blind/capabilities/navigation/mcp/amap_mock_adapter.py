"""业务侧 AMap MCP adapter。"""

from __future__ import annotations

import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any

from openaiglasses import BaseMcpAdapter, CapabilityResult, McpMethodSpec
from pydantic import BaseModel, Field


class AmapRoutePlanInput(BaseModel):
    """AMap 路线规划输入。"""

    origin: str = Field(default="", description="路线起点名称或坐标；不知道时可以留空表示当前位置。")
    destination: str = Field(description="路线终点名称或坐标，应使用已确认的目的地。")
    destination_name: str = Field(default="", description="路线终点展示名称；destination 为坐标时用于播报。")
    strategy: str = Field(default="walking", description="路线策略；盲人步行导航通常使用 walking。")


class AmapPoiSearchInput(BaseModel):
    """AMap POI 搜索输入。"""

    keyword: str = Field(description="用户想去的地点关键词，例如商场、医院、地铁站或具体店名。")
    city: str = Field(default="", description="限定搜索的城市；用户没有说明城市时可以留空。")


class AmapGeocodeInput(BaseModel):
    """AMap 地理编码输入。"""

    poi_id: str = Field(default="", description="已确认目的地候选的 POI 编号；没有编号时可以留空。")
    address: str = Field(default="", description="要解析坐标的地址或地点名称；没有 POI 编号时必须填写。")


class AmapMcpAdapter(BaseMcpAdapter):
    """AMap MCP adapter。

    主要功能：
    1. 在业务工程内提供 `amap.poi_search`、`amap.geocode` 和 `amap.route_plan` 方法。
    2. 验证业务 Tool 通过 SDK `context.mcp(...)` 入口调用 MCP。
    3. 配置 `AMAP_API_KEY` 时调用高德 Web 服务；未配置时回退到稳定 mock 数据。

    主要方法：
    1. `list_methods`：声明可调用 MCP 方法。
    2. `invoke`：返回结构化地址候选、地理编码和路线规划结果。
    """

    adapter_name = "blind_navigation_amap"
    _BASE_URL = "https://restapi.amap.com/v3"

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
                description="通过高德地图搜索目的地候选，用于让用户确认具体目的地。",
                input_model=AmapPoiSearchInput,
                tags=["navigation", "amap"],
            ),
            McpMethodSpec(
                name="amap.geocode",
                description="通过高德地图把用户确认的目的地候选或地址解析成坐标。",
                input_model=AmapGeocodeInput,
                tags=["navigation", "amap"],
            ),
            McpMethodSpec(
                name="amap.route_plan",
                description="通过高德地图在起点和已确认目的地之间规划步行路线。",
                input_model=AmapRoutePlanInput,
                tags=["navigation", "amap"],
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

        if self._api_key:
            real_result = self._invoke_real(method_name=method_name, input_data=input_data)
            if real_result.ok or not self._allow_mock_fallback:
                return real_result

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
            meta={"adapter": self.adapter_name, "provider": "amap_mock", "mock": True},
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
            meta={"adapter": self.adapter_name, "provider": "amap_mock", "mock": True},
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
            meta={"adapter": self.adapter_name, "provider": "amap_mock", "mock": True},
        )

    @property
    def _api_key(self) -> str:
        """读取高德 API Key。

        返回值：
        1. 优先读取 `AMAP_API_KEY`，其次兼容 `AMAP_MAPS_API_KEY`。
        """

        return str(os.getenv("AMAP_API_KEY") or os.getenv("AMAP_MAPS_API_KEY") or "").strip()

    @property
    def _allow_mock_fallback(self) -> bool:
        """判断真实高德调用失败时是否允许回退 mock。

        返回值：
        1. 默认允许，设置 `AMAP_DISABLE_MOCK_FALLBACK=true` 后关闭。
        """

        raw = str(os.getenv("AMAP_DISABLE_MOCK_FALLBACK") or "").strip().lower()
        return raw not in {"1", "true", "yes", "on"}

    def _invoke_real(self, *, method_name: str, input_data) -> CapabilityResult:
        """执行真实高德 Web 服务调用。

        参数：
        1. `method_name`：SDK MCP 方法名。
        2. `input_data`：已校验输入模型。

        返回值：
        1. `CapabilityResult`：成功时返回标准业务结构，失败时返回结构化错误。

        异常情况：
        1. 网络、鉴权和服务端错误都会转成失败结果，不向业务 Tool 抛出底层异常。
        """

        try:
            if method_name == "amap.poi_search":
                return self._invoke_real_poi_search(input_data)
            if method_name == "amap.geocode":
                return self._invoke_real_geocode(input_data)
            if method_name == "amap.route_plan":
                return self._invoke_real_route_plan(input_data)
        except Exception as exc:
            return CapabilityResult.failed(
                code="amap_request_failed",
                message="真实高德地图调用失败",
                details={"method_name": method_name, "reason": str(exc)},
                meta={"adapter": self.adapter_name, "provider": "amap"},
            )
        return CapabilityResult.failed(
            code="unsupported_mcp_method",
            message=f"不支持的 AMap MCP 方法: {method_name}",
            details={"method_name": method_name},
            meta={"adapter": self.adapter_name, "provider": "amap"},
        )

    def _invoke_real_poi_search(self, input_data) -> CapabilityResult:
        """调用高德关键字搜索。"""

        keyword = str(input_data.keyword or "").strip()
        if not keyword:
            return CapabilityResult.failed(code="invalid_input", message="keyword 不能为空")
        payload = self._get_json(
            "/place/text",
            {
                "keywords": keyword,
                "city": str(input_data.city or "").strip(),
                "offset": "5",
                "page": "1",
                "extensions": "base",
            },
        )
        pois = payload.get("pois") if isinstance(payload.get("pois"), list) else []
        candidates = []
        for poi in pois[:5]:
            if not isinstance(poi, dict):
                continue
            location = str(poi.get("location") or "").strip()
            candidates.append(
                {
                    "poi_id": str(poi.get("id") or ""),
                    "name": str(poi.get("name") or keyword),
                    "address": self._normalize_amap_text(poi.get("address")),
                    "city": self._normalize_amap_text(poi.get("cityname")),
                    "district": self._normalize_amap_text(poi.get("adname")),
                    "location": location,
                    "confidence": 0.9 if location else 0.6,
                }
            )
        return CapabilityResult.success(
            data={
                "keyword": keyword,
                "city": str(input_data.city or "").strip(),
                "candidates": candidates,
                "candidate_count": len(candidates),
            },
            message=f"找到 {len(candidates)} 个目的地候选",
            meta={"adapter": self.adapter_name, "provider": "amap", "mock": False},
        )

    def _invoke_real_geocode(self, input_data) -> CapabilityResult:
        """调用高德地理编码。"""

        address = str(input_data.address or "").strip()
        poi_id = str(input_data.poi_id or "").strip()
        if not address and not poi_id:
            return CapabilityResult.failed(
                code="invalid_input",
                message="poi_id 和 address 不能同时为空",
                details={"fields": ["poi_id", "address"]},
            )
        payload = self._get_json(
            "/geocode/geo",
            {
                "address": address or poi_id,
                "city": str(os.getenv("AMAP_DEFAULT_CITY") or "").strip(),
            },
        )
        geocodes = payload.get("geocodes") if isinstance(payload.get("geocodes"), list) else []
        if not geocodes:
            return CapabilityResult.failed(
                code="geocode_not_found",
                message="高德地图未解析到目的地坐标",
                details={"address": address, "poi_id": poi_id},
                meta={"adapter": self.adapter_name, "provider": "amap"},
            )
        item = dict(geocodes[0])
        return CapabilityResult.success(
            data={
                "poi_id": poi_id,
                "name": address or poi_id,
                "address": self._normalize_amap_text(item.get("formatted_address")) or address,
                "city": self._normalize_amap_text(item.get("city")),
                "district": self._normalize_amap_text(item.get("district")),
                "location": str(item.get("location") or "").strip(),
            },
            message=f"已解析目的地坐标：{address or poi_id}",
            meta={"adapter": self.adapter_name, "provider": "amap", "mock": False},
        )

    def _invoke_real_route_plan(self, input_data) -> CapabilityResult:
        """调用高德步行路线规划。"""

        origin = self._resolve_route_point(str(input_data.origin or "").strip(), point_name="origin")
        destination = self._resolve_route_point(
            str(input_data.destination or "").strip(),
            point_name="destination",
            display_name=str(getattr(input_data, "destination_name", "") or "").strip(),
        )
        payload = self._get_json(
            "/direction/walking",
            {
                "origin": origin["location"],
                "destination": destination["location"],
            },
        )
        route_payload = payload.get("route") if isinstance(payload.get("route"), dict) else {}
        paths = route_payload.get("paths") if isinstance(route_payload.get("paths"), list) else []
        if not paths:
            return CapabilityResult.failed(
                code="route_not_found",
                message="高德地图未返回可用步行路线",
                details={"origin": origin, "destination": destination},
                meta={"adapter": self.adapter_name, "provider": "amap"},
            )
        path = dict(paths[0])
        steps = []
        for step in path.get("steps") or []:
            if not isinstance(step, dict):
                continue
            steps.append(
                {
                    "instruction": str(step.get("instruction") or "").strip(),
                    "distance_meters": self._safe_int(step.get("distance")),
                    "duration_seconds": self._safe_int(step.get("duration")),
                    "road": str(step.get("road") or "").strip(),
                }
            )
        distance_meters = self._safe_int(path.get("distance"))
        duration_seconds = self._safe_int(path.get("duration"))
        duration_minutes = max(1, round(duration_seconds / 60)) if duration_seconds else 0
        summary = (
            f"从{origin['name']}步行到{destination['name']}，约{distance_meters}米，预计{duration_minutes}分钟"
        )
        return CapabilityResult.success(
            data={
                "provider": "amap",
                "origin": origin["name"],
                "origin_location": origin["location"],
                "destination": destination["name"],
                "destination_location": destination["location"],
                "strategy": str(input_data.strategy or "walking").strip() or "walking",
                "distance_meters": distance_meters,
                "duration_minutes": duration_minutes,
                "summary": summary,
                "steps": steps,
            },
            message=summary,
            meta={"adapter": self.adapter_name, "provider": "amap", "mock": False},
        )

    def _resolve_route_point(self, value: str, *, point_name: str, display_name: str = "") -> dict[str, str]:
        """把路线点解析成高德经纬度。

        参数：
        1. `value`：用户提供的起点或终点，可以是经纬度、地点名称或“当前位置”。
        2. `point_name`：字段名，用于错误说明。

        返回值：
        1. 包含 `name` 和 `location` 的字典。
        """

        normalized = value or ("当前位置" if point_name == "origin" else "")
        default_origin = str(os.getenv("AMAP_DEFAULT_ORIGIN") or "").strip()
        if point_name == "origin" and normalized in {"", "当前位置"} and default_origin:
            return {"name": "当前位置", "location": default_origin}
        if self._is_lnglat(normalized):
            return {"name": display_name or normalized, "location": normalized}
        geocode = self._invoke_real_geocode(type("Input", (), {"address": normalized, "poi_id": ""})())
        if not geocode.ok:
            raise RuntimeError(geocode.message or f"{point_name} 解析失败")
        location = str(geocode.data.get("location") or "").strip()
        if not location:
            raise RuntimeError(f"{point_name} 缺少可用坐标")
        return {"name": normalized, "location": location}

    def _get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """发送高德 Web 服务请求并返回 JSON。

        参数：
        1. `path`：接口路径。
        2. `params`：业务参数；函数会自动追加 `key`。

        返回值：
        1. JSON 字典。
        """

        query = {key: value for key, value in params.items() if value not in {"", None}}
        query["key"] = self._api_key
        url = f"{self._BASE_URL}{path}?{urlencode(query)}"
        request = Request(url, headers={"User-Agent": "openaiglasses-business/0.1"})
        with urlopen(request, timeout=float(os.getenv("AMAP_HTTP_TIMEOUT_SECONDS") or "6")) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if str(payload.get("status") or "") != "1":
            raise RuntimeError(
                f"AMap error infocode={payload.get('infocode')} info={payload.get('info')}"
            )
        return dict(payload)

    @staticmethod
    def _is_lnglat(value: str) -> bool:
        """判断字符串是否为高德经纬度。"""

        parts = [part.strip() for part in value.split(",")]
        if len(parts) != 2:
            return False
        try:
            float(parts[0])
            float(parts[1])
        except ValueError:
            return False
        return True

    @staticmethod
    def _safe_int(value: Any) -> int:
        """把高德返回的数字字段转成整数。"""

        try:
            return int(float(str(value or "0")))
        except ValueError:
            return 0

    @staticmethod
    def _normalize_amap_text(value: Any) -> str:
        """把高德可能返回的列表或字符串字段转成普通字符串。"""

        if isinstance(value, list):
            return " ".join(str(item) for item in value if str(item).strip())
        return str(value or "").strip()

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


MockAmapMcpAdapter = AmapMcpAdapter
