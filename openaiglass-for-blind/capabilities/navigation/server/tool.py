"""导航准备 Tool。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openaiglasses import BaseTool, CapabilityResult


class PrepareNavigationInput(BaseModel):
    """导航准备 Tool 输入。"""

    origin: str = Field(default="", description="起点，默认为当前位置")
    destination: str = Field(description="目的地")
    strategy: str = Field(default="walking", description="路线策略，例如 walking")
    create_task: bool = Field(default=True, description="是否基于路线创建导航任务")


class PrepareNavigationOutput(BaseModel):
    """导航准备 Tool 输出。"""

    route: dict[str, Any]
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
    description = "准备步行导航路线，并可创建导航任务"
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
        strategy = str(input_data.get("strategy") or "walking").strip() or "walking"
        route_result = context.mcp(
            "amap.route_plan",
            {
                "origin": origin,
                "destination": destination,
                "strategy": strategy,
            },
        )
        if not route_result.ok:
            return route_result

        route = dict(route_result.data)
        data: dict[str, Any] = {"route": route}
        if bool(input_data.get("create_task", True)):
            task = context.create_task(
                task_type="navigation_task",
                input_data={
                    "origin": origin,
                    "destination": destination,
                    "strategy": strategy,
                    "route": route,
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
            message=str(route.get("summary") or f"已准备到{destination}的导航路线"),
        )

