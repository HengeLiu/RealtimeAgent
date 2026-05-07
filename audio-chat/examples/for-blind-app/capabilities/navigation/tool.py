from __future__ import annotations

from audio_chat import BaseTool, ToolContext, ToolResult


class PrepareNavigationTool(BaseTool):
    """导航路线准备 Tool。

    主要功能：
    1. 调用 MCP mock 路线规划工具。
    2. 返回路线摘要，供 Agent 决定是否启动导航 Task。
    3. MCP 不直接持有设备上下文，设备能力仍由 Task 通过 event + stream 使用。
    """

    name = "prepare_navigation"
    description = "准备导航目的地、POI 和路线"
    progress_message = "正在规划路线"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行路线准备。

        主要逻辑：优先调用 `amap.route_plan` MCP mock；不可用时返回明确 fallback。
        参数：`input_data` 可包含 `destination`、`origin`。
        返回值：路线规划摘要。
        异常情况：MCP 未启用或失败时返回 fallback，不伪装真实地图成功。
        """

        destination = str(input_data.get("destination") or "盲人服务中心")
        origin = str(input_data.get("origin") or "当前位置")
        if context.mcp is None:
            return ToolResult.success(
                data={"provider": "fallback", "destination": destination, "route_ready": False},
                message="MCP 未配置，无法规划真实路线",
            )
        try:
            route = context.mcp.call(
                tool_name="amap.route_plan",
                arguments={"origin": origin, "destination": destination},
                timeout_seconds=float(input_data.get("timeout_seconds") or 5),
            )
        except Exception as exc:
            return ToolResult.success(
                data={"provider": "fallback", "destination": destination, "route_ready": False, "error": str(exc)},
                message="路线规划进入 fallback",
            )
        return ToolResult.success(data={"route_ready": True, "route": route}, message="路线已准备")


class StartNavigationTool(BaseTool):
    """启动导航执行期 Task 的 Tool。"""

    name = "start_navigation"
    description = "启动导航执行期任务"
    progress_message = "正在启动导航"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """创建导航任务。"""

        if context.tasks is None:
            return ToolResult.success(data={"started": False, "reason": "task_engine_unavailable"})
        ref = await context.tasks.create(
            task_type="navigation_task",
            user_id=context.user_id,
            session_id=context.session_id,
            input_data=dict(input_data),
            summary="导航任务",
        )
        return ToolResult.success(data={"started": True, "task_id": ref.task_id, "state": ref.state}, tasks=[ref])
