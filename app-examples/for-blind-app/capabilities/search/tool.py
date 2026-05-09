from __future__ import annotations

from pydantic import BaseModel, Field

from audio_chat import BaseTool, ToolContext, ToolResult, ToolSpec


class SearchWebInput(BaseModel):
    """搜索 Tool 输入参数。"""

    query: str = Field(default="盲人导航安全提示", description="要搜索的问题或关键词。")
    limit: int = Field(default=3, ge=1, le=10, description="最多返回的搜索结果数量。")
    timeout_seconds: float = Field(default=5, gt=0, description="等待搜索结果的超时时间，单位秒。")


class SearchWebOutput(BaseModel):
    """搜索 Tool 输出结构。"""

    provider: str | None = Field(default=None, description="搜索结果来源。")
    fallback: bool | None = Field(default=None, description="是否使用 fallback。")
    query: str = Field(description="实际搜索词。")
    items: list[dict] | None = Field(default=None, description="搜索结果列表。")
    search: dict | None = Field(default=None, description="搜索返回的原始结构化结果。")
    error: str | None = Field(default=None, description="fallback 错误说明。")


class SearchWebTool(BaseTool):
    """搜索 MCP wrapper Tool。"""

    spec = ToolSpec(
        name="search_web",
        description="当用户明确要求搜索、查询资料、查最新公开信息，或问题需要外部资料时调用。",
        input_model=SearchWebInput,
        output_model=SearchWebOutput,
        progress_message=("我查一下资料。", "稍等，我搜索一下。"),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行搜索。

        主要逻辑：调用 `web.search` MCP mock；没有真实 key 时返回明确 fallback 来源。
        参数：`input_data` 包含 `query`。
        返回值：搜索摘要和引用列表。
        异常情况：MCP 不可用时返回 fallback 结果，不把大正文塞进控制事件。
        """

        query = input_data["query"]
        mcp = getattr(context, "mcp", None)
        if mcp is None:
            return ToolResult.success(
                data={"provider": "fallback", "fallback": True, "query": query, "items": []},
                message="搜索服务未配置，暂时没有搜索结果",
            )
        try:
            result = mcp.call(
                tool_name="web.search",
                arguments={"query": query, "limit": int(input_data["limit"])},
                timeout_seconds=float(input_data["timeout_seconds"]),
            )
        except Exception as exc:
            return ToolResult.success(
                data={"provider": "fallback", "fallback": True, "query": query, "error": str(exc), "items": []},
                message="搜索服务暂时不可用",
            )
        return ToolResult.success(data={"query": query, "search": result}, message="搜索完成")
