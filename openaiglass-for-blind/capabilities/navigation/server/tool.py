"""导航准备 Tool。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openaiglasses import BaseTool, CapabilityResult


class PrepareNavigationInput(BaseModel):
    """导航准备 Tool 输入。"""

    origin: str = Field(default="", description="导航起点；通常留空表示从用户当前位置出发。")
    destination: str = Field(description="用户想去的目的地名称或地址，例如“附近地铁站”“人民医院”。")
    city: str = Field(default="", description="用于缩小目的地搜索范围的城市；用户没有说明城市时可以留空。")
    strategy: str = Field(default="walking", description="路线策略；盲人眼镜场景通常使用 walking。")
    selected_poi_id: str = Field(
        default="",
        description="用户从候选目的地中确认的 POI 编号；首次搜索或用户未确认时留空。",
    )
    require_confirmation: bool = Field(
        default=False,
        description="如果目的地可能有多个候选且需要用户先确认，填 true；否则填 false 继续创建路线。",
    )
    create_task: bool = Field(
        default=True,
        description="是否在路线准备完成后启动导航后台任务；用户只问路线信息时可填 false。",
    )


class PrepareNavigationOutput(BaseModel):
    """导航准备 Tool 输出。"""

    route: dict[str, Any]
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    selected_poi: dict[str, Any] | None = None
    task_id: str | None = None
    task_type: str | None = None
    state: str | None = None


class PrepareNavigationTool(BaseTool):
    """准备导航路线的工具。

    主要功能：
    1. 校验导航目的地。
    2. 通过 SDK `context.mcp(...)` 调用已注册的 AMap MCP 方法。
    3. 按需创建 SDK 托管 `navigation_task`。

    主要方法：
    1. `run`：执行路线准备和任务创建。
    """

    name = "prepare_navigation"
    description = (
        "当用户要求去某个地点、准备步行导航或查询到目的地的路线时调用。"
        "如果目的地不明确，可先要求确认候选；用户没有导航意图时不要调用。"
    )
    input_model = PrepareNavigationInput
    output_model = PrepareNavigationOutput

    def run(self, context, input_data: dict[str, Any]) -> CapabilityResult:
        """准备导航路线。

        参数：
        1. `context`：SDK 设备组上下文。
        2. `input_data`：业务输入，包含起点、目的地和路线策略。

        返回值：
        1. `CapabilityResult`：路线规划结果和可选导航任务信息。

        异常情况：
        1. 目的地为空时返回结构化失败结果。
        2. MCP 调用失败时直接返回 SDK 统一失败结果。
        """

        destination = str(input_data.get("destination") or "").strip()
        if not destination:
            return CapabilityResult.failed(code="invalid_input", message="destination 不能为空")

        origin = str(input_data.get("origin") or "当前位置").strip() or "当前位置"
        city = str(input_data.get("city") or "").strip()
        strategy = str(input_data.get("strategy") or "walking").strip() or "walking"
        selected_poi_id = str(input_data.get("selected_poi_id") or "").strip()
        require_confirmation = bool(input_data.get("require_confirmation", False))
        poi_result = context.mcp(
            "amap.poi_search",
            {
                "keyword": destination,
                "city": city,
            },
        )
        if not poi_result.ok:
            return poi_result

        candidates = list(poi_result.data.get("candidates") or [])
        selected_poi = self._select_poi(candidates=candidates, selected_poi_id=selected_poi_id)
        if require_confirmation and not selected_poi_id:
            return CapabilityResult.success(
                data={
                    "route": {},
                    "candidates": candidates,
                    "awaiting_confirmation": True,
                    "confirmation_prompt": self._build_confirmation_prompt(candidates),
                },
                message="已找到目的地候选，请确认要导航到哪一个",
            )
        if selected_poi is None:
            return CapabilityResult.failed(
                code="poi_not_found",
                message="未找到可用目的地候选",
                details={"destination": destination, "selected_poi_id": selected_poi_id},
            )

        geocode_result = context.mcp(
            "amap.geocode",
            {
                "poi_id": str(selected_poi.get("poi_id") or ""),
                "address": str(selected_poi.get("name") or destination),
            },
        )
        if not geocode_result.ok:
            return geocode_result

        resolved_destination = str(
            geocode_result.data.get("name") or selected_poi.get("name") or destination
        )
        route_result = context.mcp(
            "amap.route_plan",
            {
                "origin": origin,
                "destination": str(geocode_result.data.get("location") or resolved_destination),
                "destination_name": resolved_destination,
                "strategy": strategy,
            },
        )
        if not route_result.ok:
            return route_result

        route = dict(route_result.data)
        data: dict[str, Any] = {
            "route": route,
            "candidates": candidates,
            "selected_poi": dict(geocode_result.data),
            "awaiting_confirmation": False,
        }
        if bool(input_data.get("create_task", True)):
            task = context.create_task(
                task_type="navigation_task",
                input_data={
                    "origin": origin,
                    "destination": resolved_destination,
                    "strategy": strategy,
                    "route": route,
                    "selected_poi": dict(geocode_result.data),
                },
            )
            data.update(
                {
                    "task_id": task.task_id,
                    "task_type": task.task_type,
                    "state": task.state,
                    "task_data": task.data,
                }
            )

        return CapabilityResult.success(
            data=data,
            message=str(route.get("summary") or f"已准备到{resolved_destination}的导航路线"),
        )

    @staticmethod
    def _select_poi(*, candidates: list[dict[str, Any]], selected_poi_id: str) -> dict[str, Any] | None:
        """选择用户确认的 POI 或默认候选。

        参数：
        1. `candidates`：POI 候选列表。
        2. `selected_poi_id`：用户确认的 POI 编号。

        返回值：
        1. 选中的 POI；没有可用候选时返回 `None`。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        if not candidates:
            return None
        if selected_poi_id:
            for candidate in candidates:
                if str(candidate.get("poi_id") or "") == selected_poi_id:
                    return dict(candidate)
            return None
        return dict(candidates[0])

    @staticmethod
    def _build_confirmation_prompt(candidates: list[dict[str, Any]]) -> str:
        """生成目的地候选确认提示。"""

        if not candidates:
            return "没有找到可确认的目的地候选"
        names = [str(item.get("name") or "") for item in candidates[:3]]
        return "请确认目的地：" + "、".join(name for name in names if name)
