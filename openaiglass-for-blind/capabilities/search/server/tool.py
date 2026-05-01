"""搜索 Tool。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openaiglasses import BaseTool, CapabilityResult


class SearchWebInput(BaseModel):
    """搜索 Tool 输入。"""

    query: str = Field(description="用户要搜索的问题或关键词。")
    max_results: int = Field(default=5, description="最多返回多少条搜索结果，默认 5 条。")


class SearchWebOutput(BaseModel):
    """搜索 Tool 输出。"""

    query: str
    results: list[dict[str, Any]]
    result_count: int


class SearchWebTool(BaseTool):
    """搜索公开网页信息的工具。

    主要功能：
    1. 接收用户搜索关键词。
    2. 通过 SDK `context.mcp("web.search", ...)` 调用搜索 adapter。
    3. 返回标题、摘要和链接，供 Agent 组织口语化回答。

    主要方法：
    1. `run`：校验输入并调用搜索 MCP。
    """

    name = "search_web"
    description = "当用户明确要求搜索、查询资料、查最新公开信息或询问需要外部资料的问题时调用。"
    input_model = SearchWebInput
    output_model = SearchWebOutput
    progress_message = [
        "我查一下资料。",
        "稍等，我搜索一下。",
        "我先帮你查一查。",
    ]

    def run(self, context, input_data: dict[str, Any]) -> CapabilityResult:
        """执行搜索。

        参数：
        1. `context`：SDK 设备组上下文。
        2. `input_data`：包含搜索关键词和结果数量。

        返回值：
        1. `CapabilityResult`：搜索结果列表。

        异常情况：
        1. 关键词为空时返回结构化失败结果。
        2. MCP 调用失败时直接返回 SDK 统一失败结果。
        """

        query = str(input_data.get("query") or "").strip()
        if not query:
            return CapabilityResult.failed(code="invalid_input", message="query 不能为空")
        max_results = max(1, min(8, int(input_data.get("max_results") or 5)))
        result = context.mcp("web.search", {"query": query, "max_results": max_results})
        if not result.ok:
            return result
        return CapabilityResult.success(
            data={
                "query": query,
                "results": list(result.data.get("results") or []),
                "result_count": int(result.data.get("result_count") or 0),
            },
            message=result.message or "搜索完成",
            meta=dict(result.meta),
        )
