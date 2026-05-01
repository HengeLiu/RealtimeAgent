# search

搜索能力用于让 Agent 在用户明确要求查询资料、搜索公开信息或问题明显需要外部资料时调用。

当前实现范围：

1. `search_web` Tool 校验搜索关键词，并调用 `context.mcp("web.search", ...)`。
2. `WebSearchMcpAdapter` 注册 `web.search` 方法，默认使用 DuckDuckGo HTML 公开页面做轻量搜索。
3. Tool 返回标题、摘要和链接，由 Agent 负责组织口语化回答。

当前不做的事情：

1. 不在业务侧引入搜索服务 SDK 依赖。
2. 不抓取全文网页正文。
3. 不绕过 SDK MCP 入口直接在 Tool 中访问网络。

验证方式：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind:. \
  uv run python -m pytest openaiglass-for-blind/tests/test_capabilities_unit.py -q
```
