# Search 搜索工具实施文档

## 1. 需求理解

搜索工具用于处理用户明确要求“查一下”“搜索一下”或问题需要外部公开资料的场景。业务层只实现搜索能力本身，搜索入口必须通过 SDK Tool 和 MCP adapter 暴露给 Agent。

## 2. 现状分析

当前 SDK 已提供：

1. `BaseTool`：把业务搜索暴露给 Agent。
2. `BaseMcpAdapter`、`McpMethodSpec`：注册业务 MCP 方法。
3. `context.mcp(...)`：Tool 调用 MCP 的统一入口。

当前 SDK 未提供通用搜索服务，因此业务侧新增 `web.search` adapter。

## 3. 实现方案

1. `SearchWebTool` 注册为 `search_web`。
2. Tool 校验 `query` 后调用 `context.mcp("web.search", ...)`。
3. `WebSearchMcpAdapter` 优先通过博查 AI Search API 获取正式搜索结果。
4. 没有配置正式 API Key 时，允许回退到 DuckDuckGo HTML 页面，仅用于本地开发验证。
5. 返回标题、摘要、链接、来源和结果数量。

选择博查 AI Search 的原因：

1. 面向 AI Agents 和 RAG 场景提供搜索 API，返回结构更适合大模型消费。
2. 国内开放平台，适合中国大陆网络环境和中文内容检索。
3. API Key 型接入，不需要在业务侧引入额外 SDK 依赖。

配置项：

```env
WEB_SEARCH_PROVIDER="auto"
BOCHA_SEARCH_API_KEY="你的博查 API Key"
BOCHA_SEARCH_API_URL="https://api.bochaai.com/v1/web-search"
BOCHA_SEARCH_FRESHNESS="noLimit"
WEB_SEARCH_TIMEOUT_SECONDS="8"
```

正式环境建议设置：

```env
WEB_SEARCH_PROVIDER="bocha"
BOCHA_SEARCH_API_KEY="你的博查 API Key"
```

## 4. 流程图

```plantuml
@startuml
title 搜索工具最小闭环

actor User as user
participant "Agent" as agent
participant "SearchWebTool" as tool
participant "DeviceGroupContext" as ctx
participant "WebSearchMcpAdapter" as search
participant "博查 AI Search" as bocha

user -> agent: 帮我查一下大模型是什么
agent -> tool: search_web(query)
tool -> ctx: mcp("web.search")
ctx -> search: invoke(query, max_results)
search -> bocha: POST /v1/web-search
bocha --> search: webPages.value[]
search --> ctx: results
ctx --> tool: CapabilityResult
tool --> agent: results
agent --> user: 组织口语化回答

@enduml
```

## 5. 自动化测试方案

单元测试覆盖：

1. `search_web` 会通过 SDK MCP 入口调用 `web.search`。
2. 搜索 adapter 能把博查 AI Search 响应解析成标题、摘要、来源和链接。
3. 搜索 adapter 仍保留 DuckDuckGo HTML 解析测试，确保本地开发 fallback 可用。

回归命令：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind:. uv run python -m pytest openaiglass-for-blind/tests/test_capabilities_unit.py -q
```

## 6. 当前方案与架构设计的契合程度

当前方案只使用 SDK 公开的 Tool、MCP adapter 和 `context.mcp(...)`，没有绕过设备组上下文，也没有修改 SDK 框架代码。

## 7. 开发后测试结果

已执行：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind:. uv run python -m pytest openaiglass-for-blind/tests/test_capabilities_unit.py -q
```

结果：通过。

## 8. 当前实现进展

已完成：

1. `search_web` Tool。
2. `web.search` MCP adapter。
3. 宿主装配。
4. 单元测试。
5. 博查 AI Search 正式 provider 接入。
6. DuckDuckGo HTML 开发 fallback。

未完成：

1. 真实 API Key 联网回归。
2. 网页全文抓取和引用质量评估。
