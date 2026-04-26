# SDK v2 迭代记录

本文记录 SDK 团队根据业务能力开发反馈进行的第二轮优化。业务侧版本记录更新为 `sdk-v3`，原因是第一轮文档名沿用 `SDK v1`，而对业务团队可见的能力版本已经进入第三个可用迭代。

## 1. 输入反馈

业务团队在 `openaiglass-for-blind/docs/stage1/develop/架构阻塞点说明与改进建议.md` 中反馈：

1. 业务 `BaseTool.run(context, input_data)` 拿到的 `DeviceGroupContext` 缺少统一 MCP 调用入口。
2. 导航准备能力如果要调用地图 MCP，只能绕过 SDK 直接 import adapter，或自行拼装 `McpRegistry / McpGateway / AgentToolContext`。
3. 完整 SDK 预检的 `sdk_boundary` 命中了 iOS SDK 测试夹具中的 `find_object_phone_task`，导致业务侧无法在不越界修改 SDK 的情况下修复预检失败。

## 2. 本轮 SDK 改动

### 2.1 `DeviceGroupContext.mcp(...)`

新增业务可见 MCP 调用入口：

```python
route = context.mcp(
    "amap.route_plan",
    {
        "origin": input_data["origin"],
        "destination": input_data["destination"],
        "strategy": input_data.get("strategy", "walking"),
    },
)
```

该入口复用 SDK 内部 `McpGateway`，并把 agent-core 的能力结果转换成业务侧统一 `CapabilityResult`。业务调用失败时返回结构化失败结果，包含 `method_name`、输入摘要和统一错误码。

### 2.2 MCP 网关绑定

`OpenAIGlassesSDK` 现在维护统一 `McpRegistry / McpGateway`，`register_mcp_adapter(...)` 会同步注册到该网关。`build_agent_facade_from_sdk(...)` 和真实 `ControlRuntime` 会把同一个 MCP 网关绑定到 `DeviceGroupRuntime`，保证：

1. 模型可见 MCP Tool 与业务 `context.mcp(...)` 走同一套注册表。
2. 离线回放、SDK Tool 调用和真实服务端运行时都能使用同一 MCP 调用入口。
3. MCP 调用轨迹可通过 `DeviceGroupRuntime.list_mcp_traces()` 观察；真实服务端中还会同步写入 agent session trace。

### 2.3 iOS SDK 测试夹具通用化

将 `openaiglass-sdk/phone-ios/GlassesVideoReceiverTests` 中的历史业务 task type 改为 `demo_phone_task`。SDK 自测仍然覆盖多能力注册、按 `taskType` 分发和当前活跃任务帧路由，但不再携带 `find_object` 业务关键词。

## 3. 本轮不进入 SDK 的内容

本轮没有在 SDK 内置真实 AMap adapter。

原因：

1. 地图供应商、鉴权、限流和路线策略属于外部能力适配，不应内建到通用 SDK 根运行时。
2. SDK 已提供 `BaseMcpAdapter`、`register_mcp_adapter(...)` 和 `context.mcp(...)`，业务或宿主项目可以把具体 adapter 作为插件注册。
3. 导航业务的路线解释、缺槽追问和产品策略仍应放在业务能力层，而不是 SDK 系统层。

## 4. 文档同步

已同步更新：

1. `openaiglass-for-blind/SDK安装与能力开发指南.md`
2. `openaiglass-for-blind/sdk-version`
3. `openaiglass-sdk/docs/structure-design/SDK公共契约设计.md`
4. `openaiglass-sdk/docs/sdk-design/SDK开发者快速开始.md`

## 5. 验证范围

本轮新增 Python 单元测试覆盖：

1. mock MCP adapter 通过 `sdk.register_mcp_adapter(...)` 注册。
2. 业务上下文通过 `DeviceGroupContext.mcp(...)` 调用 MCP 方法。
3. 调用结果以业务侧 `CapabilityResult` 返回。
4. MCP 调用轨迹写入 `DeviceGroupRuntime.list_mcp_traces()`。

本轮预检重点：

1. `sdk_boundary` 不再命中 iOS SDK 测试夹具中的 `find_object`。
2. 完整预检不需要业务团队使用 `--skip-boundary`。
