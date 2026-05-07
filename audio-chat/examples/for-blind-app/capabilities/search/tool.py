from __future__ import annotations

from audio_chat import BaseTool, ToolContext, ToolResult


class SearchWebTool(BaseTool):
    """搜索 MCP wrapper Tool。"""

    name = "search_web"
    description = "调用搜索 MCP mock，返回可引用摘要"
    progress_message = "正在搜索资料"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行搜索。

        主要逻辑：调用 `web.search` MCP mock；没有真实 key 时返回明确 fallback 来源。
        参数：`input_data` 包含 `query`。
        返回值：搜索摘要和引用列表。
        异常情况：MCP 不可用时返回 fallback 结果，不把大正文塞进控制事件。
        """

        query = str(input_data.get("query") or "盲人导航安全提示")
        if context.mcp is None:
            return ToolResult.success(
                data={"provider": "fallback", "fallback": True, "query": query, "items": []},
                message="MCP 未配置，搜索使用空 fallback",
            )
        try:
            result = context.mcp.call(
                tool_name="web.search",
                arguments={"query": query, "limit": int(input_data.get("limit") or 3)},
                timeout_seconds=float(input_data.get("timeout_seconds") or 5),
            )
        except Exception as exc:
            return ToolResult.success(
                data={"provider": "fallback", "fallback": True, "query": query, "error": str(exc), "items": []},
                message="搜索 provider 不可用，已返回 fallback",
            )
        return ToolResult.success(data={"query": query, "search": result}, message="搜索完成")
