"""业务侧 AMap MCP mock adapter。"""

from __future__ import annotations

from typing import Any

from agent_core.mcp import BaseMcpAdapter
from agent_core.models import CapabilityResult, McpMethodSpec
from pydantic import BaseModel, Field


class AmapRoutePlanInput(BaseModel):
    """AMap 路线规划输入。"""

    origin: str = Field(default="", description="起点名称或坐标")
    destination: str = Field(description="终点名称或坐标")
    strategy: str = Field(default="walking", description="路线策略")


class MockAmapMcpAdapter(BaseMcpAdapter):
    """AMap MCP mock adapter。

    主要功能：
    1. 在业务工程内提供可回放的 `amap.route_plan` 方法。
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
        1. `McpMethodSpec` 列表，目前只包含 `amap.route_plan`。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        return [
            McpMethodSpec(
                name="amap.route_plan",
                description="规划步行导航路线",
                input_model=AmapRoutePlanInput,
                tags=["navigation", "amap", "mock"],
            )
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

        if method_name != "amap.route_plan":
            return CapabilityResult.failed(
                code="unsupported_mcp_method",
                message=f"不支持的 AMap MCP 方法: {method_name}",
                details={"method_name": method_name},
            )

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

