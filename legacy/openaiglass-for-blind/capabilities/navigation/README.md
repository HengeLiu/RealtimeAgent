# navigation

导航能力用于验证 SDK 提供的业务上下文 MCP 调用入口，并把路线准备结果沉淀为 SDK 托管任务。

当前实现范围：

1. `prepare_navigation` Tool 校验目的地，并调用 `context.mcp("amap.poi_search" / "amap.geocode" / "amap.route_plan", ...)`。
2. `AmapMcpAdapter` 在配置 `AMAP_API_KEY` 时调用高德 Web 服务；未配置时回退到稳定 mock 数据。
3. `navigation_task` 保存路线、提交路线准备通知，并支持进展事件、到达事件和取消。
4. 设备级回放和真机联调用于验证 Tool、MCP trace、任务状态和通知。

当前不做的事情：

1. 不直接 import SDK 内部 AMap adapter。
2. 不在业务工程里拼 `McpGateway` 或 `AgentToolContext`。
3. 不实现最后 10 米导航策略；当前只接入红绿灯视觉事件的最小策略。
4. 不修改眼镜端或手机端 SDK 框架代码。

真实高德调用需要在 `config/local_server.yaml` 中配置非敏感参数，并把 `AMAP_API_KEY` 写入 `config/.env` 或启动 shell：

```yaml
business:
  navigation:
    amap:
      default_city: "上海"
      default_origin: "121.412000,31.169000"
      disable_mock_fallback: false
      http_timeout_seconds: 6
```

其中 `AMAP_DEFAULT_ORIGIN` 是没有端侧定位时的临时起点坐标。真实产品应由 SDK 设备上下文提供当前位置。

验证时启动真实服务端，并按需使用真实 iOS phone 与 `glass-playback` 设备级回放。
