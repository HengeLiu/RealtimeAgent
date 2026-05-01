"""业务侧网页搜索 MCP adapter。"""

from __future__ import annotations

import html
import json
import os
import re
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from openaiglasses import BaseMcpAdapter, CapabilityResult, McpMethodSpec
from pydantic import BaseModel, Field


class WebSearchInput(BaseModel):
    """网页搜索输入。"""

    query: str = Field(description="用户要搜索的问题或关键词。")
    max_results: int = Field(default=5, description="最多返回多少条搜索结果。")


class WebSearchMcpAdapter(BaseMcpAdapter):
    """网页搜索 MCP adapter。

    主要功能：
    1. 在业务层注册 `web.search` MCP 方法。
    2. 优先通过博查 AI Search API 做正式搜索。
    3. 未配置正式搜索 API Key 时，可回退到 DuckDuckGo HTML 页面用于本地开发验证。
    4. 网络不可用时返回结构化错误，便于 Agent 告知用户稍后再试。

    主要方法：
    1. `list_methods`：声明搜索方法。
    2. `invoke`：校验输入并执行搜索。
    """

    adapter_name = "blind_web_search"

    def list_methods(self) -> list[McpMethodSpec]:
        """列出当前 adapter 支持的 MCP 方法。"""

        return [
            McpMethodSpec(
                name="web.search",
                description="搜索互联网上的公开信息，适合用户询问新闻、百科、天气之外的一般资料或需要查资料的问题。",
                input_model=WebSearchInput,
                tags=["search", "web"],
            )
        ]

    def invoke(self, *, method_name: str, context, input_data) -> CapabilityResult:
        """执行网页搜索。

        参数：
        1. `method_name`：MCP 方法名，目前只支持 `web.search`。
        2. `context`：SDK MCP 调用上下文。
        3. `input_data`：已通过 pydantic 校验的输入对象。

        返回值：
        1. 成功时返回搜索结果列表；失败时返回结构化错误。

        异常情况：
        1. 网络异常、解析异常和不支持的方法都会转换成失败结果。
        """

        if method_name != "web.search":
            return CapabilityResult.failed(
                code="unsupported_mcp_method",
                message=f"不支持的搜索 MCP 方法: {method_name}",
                details={"method_name": method_name},
            )
        query = str(input_data.query or "").strip()
        if not query:
            return CapabilityResult.failed(code="invalid_input", message="query 不能为空")
        max_results = max(1, min(8, int(input_data.max_results or 5)))
        provider = self._resolve_provider()
        try:
            results = self._search(query=query, max_results=max_results, provider=provider)
        except Exception as exc:
            return CapabilityResult.failed(
                code="web_search_failed",
                message="网页搜索失败",
                details={"query": query, "provider": provider, "reason": str(exc)},
                meta={"adapter": self.adapter_name, "provider": provider},
            )
        return CapabilityResult.success(
            data={"query": query, "results": results, "result_count": len(results)},
            message=f"找到 {len(results)} 条搜索结果",
            meta={"adapter": self.adapter_name, "provider": provider},
        )

    def _resolve_provider(self) -> str:
        """解析搜索服务提供方。

        参数：
        1. 无。

        返回值：
        1. `bocha`：使用博查 AI Search 正式 API。
        2. `duckduckgo_html`：使用 DuckDuckGo HTML 开发 fallback。

        异常情况：
        1. 当显式要求 `bocha` 但未配置 API Key 时，后续调用会返回结构化失败。
        """

        provider = str(os.getenv("WEB_SEARCH_PROVIDER") or "auto").strip().lower()
        if provider in {"", "auto"}:
            return "bocha" if self._bocha_api_key() else "duckduckgo_html"
        return provider

    def _search(self, *, query: str, max_results: int, provider: str) -> list[dict[str, Any]]:
        """按 provider 执行搜索。

        参数：
        1. `query`：搜索关键词。
        2. `max_results`：最大结果数。
        3. `provider`：搜索服务提供方。

        返回值：
        1. 统一结构的搜索结果列表。

        异常情况：
        1. 不支持的 provider 会抛出 `ValueError`。
        """

        if provider == "bocha":
            return self._search_bocha(query=query, max_results=max_results)
        if provider == "duckduckgo_html":
            return self._search_duckduckgo(query=query, max_results=max_results)
        raise ValueError(f"不支持的搜索服务 provider: {provider}")

    def _search_bocha(self, *, query: str, max_results: int) -> list[dict[str, Any]]:
        """调用博查 AI Search API 并解析结果。

        参数：
        1. `query`：搜索关键词。
        2. `max_results`：最大结果数。

        返回值：
        1. 搜索结果列表，每项包含标题、摘要、链接和来源信息。

        异常情况：
        1. 未配置 API Key、HTTP 请求失败或接口返回错误时抛出异常，由 `invoke` 转成结构化失败。
        """

        api_key = self._bocha_api_key()
        if not api_key:
            raise RuntimeError("未配置 BOCHA_SEARCH_API_KEY 或 BOCHA_API_KEY")
        url = os.getenv("BOCHA_SEARCH_API_URL") or "https://api.bochaai.com/v1/web-search"
        payload = {
            "query": query,
            "summary": True,
            "freshness": os.getenv("BOCHA_SEARCH_FRESHNESS") or "noLimit",
            "count": max_results,
        }
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "openaiglasses-business-search/0.1",
            },
            method="POST",
        )
        with urlopen(request, timeout=float(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS") or "8")) as response:
            body = response.read().decode("utf-8", errors="replace")
        return self._parse_bocha_response(json.loads(body), max_results=max_results)

    @staticmethod
    def _bocha_api_key() -> str:
        """读取博查搜索 API Key。

        参数：
        1. 无。

        返回值：
        1. 配置的 API Key；未配置时返回空字符串。
        """

        return str(os.getenv("BOCHA_SEARCH_API_KEY") or os.getenv("BOCHA_API_KEY") or "").strip()

    @staticmethod
    def _parse_bocha_response(payload: dict[str, Any], *, max_results: int) -> list[dict[str, Any]]:
        """解析博查 AI Search API 返回结果。

        参数：
        1. `payload`：接口 JSON 响应。
        2. `max_results`：最大结果数。

        返回值：
        1. 统一结构的搜索结果列表。

        异常情况：
        1. 当接口 code 非 200 时抛出 `RuntimeError`。
        """

        code = payload.get("code")
        if code not in (None, 200):
            raise RuntimeError(str(payload.get("msg") or payload.get("message") or f"Bocha API 返回错误 code={code}"))
        data = payload.get("data") or {}
        web_pages = data.get("webPages") or {}
        values = web_pages.get("value") or []
        results: list[dict[str, Any]] = []
        for item in values:
            title = str(item.get("name") or item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            snippet = str(item.get("summary") or item.get("snippet") or "").strip()
            if not title or not url:
                continue
            result = {
                "title": title,
                "snippet": snippet,
                "url": url,
            }
            site_name = str(item.get("siteName") or "").strip()
            display_url = str(item.get("displayUrl") or "").strip()
            if site_name:
                result["source"] = site_name
            if display_url:
                result["display_url"] = display_url
            results.append(result)
            if len(results) >= max_results:
                break
        return results

    def _search_duckduckgo(self, *, query: str, max_results: int) -> list[dict[str, Any]]:
        """调用 DuckDuckGo HTML 搜索并解析结果。

        参数：
        1. `query`：搜索关键词。
        2. `max_results`：最大结果数。

        返回值：
        1. 搜索结果列表，每项包含标题、摘要和链接。

        异常情况：
        1. 网络异常或页面结构变化时可能抛出异常，由 `invoke` 转成结构化失败。
        """

        url = "https://duckduckgo.com/html/?" + urlencode({"q": query, "kl": "cn-zh"})
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 openaiglasses-business-search/0.1",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urlopen(request, timeout=float(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS") or "8")) as response:
            body = response.read().decode("utf-8", errors="replace")
        return self._parse_duckduckgo_html(body, max_results=max_results)

    @staticmethod
    def _parse_duckduckgo_html(body: str, *, max_results: int) -> list[dict[str, Any]]:
        """解析 DuckDuckGo HTML 结果。

        参数：
        1. `body`：搜索结果 HTML。
        2. `max_results`：最大结果数。

        返回值：
        1. 规范化搜索结果列表。
        """

        results: list[dict[str, Any]] = []
        blocks = re.findall(r'<div class="result__body">(.*?)</div>\s*</div>', body, flags=re.S)
        if not blocks:
            blocks = re.findall(r'<a rel="nofollow" class="result__a".*?</a>.*?(?=<a rel="nofollow" class="result__a"|$)', body, flags=re.S)
        for block in blocks:
            title_match = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S)
            if not title_match:
                continue
            href = html.unescape(title_match.group(1))
            title = WebSearchMcpAdapter._clean_html(title_match.group(2))
            snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>|class="result__snippet"[^>]*>(.*?)</div>', block, flags=re.S)
            snippet = ""
            if snippet_match:
                snippet = WebSearchMcpAdapter._clean_html(snippet_match.group(1) or snippet_match.group(2) or "")
            if not title or not href:
                continue
            results.append({"title": title, "snippet": snippet, "url": href})
            if len(results) >= max_results:
                break
        return results

    @staticmethod
    def _clean_html(value: str) -> str:
        """清理 HTML 标签和实体。"""

        text = re.sub(r"<[^>]+>", " ", value)
        text = html.unescape(text)
        return re.sub(r"\s+", " ", text).strip()


def dump_search_results_for_debug(results: list[dict[str, Any]]) -> str:
    """把搜索结果转成调试用 JSON 字符串。

    参数：
    1. `results`：搜索结果列表。

    返回值：
    1. 不转义中文的 JSON 字符串。
    """

    return json.dumps(results, ensure_ascii=False, indent=2)
