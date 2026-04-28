# navigation

导航能力用于验证 `sdk-v19` 提供的业务上下文 MCP 调用入口，并把路线准备结果沉淀为 SDK 托管任务。

当前实现范围：

1. `prepare_navigation` Tool 校验目的地，并调用 `context.mcp("amap.poi_search" / "amap.geocode" / "amap.route_plan", ...)`。
2. `MockAmapMcpAdapter` 在业务工程内提供稳定的 AMap mock 路线规划结果。
3. `navigation_task` 保存路线、提交路线准备通知，并支持进展事件、到达事件和取消。
4. 设备级回放和真机联调用于验证 Tool、MCP trace、任务状态和通知。

当前不做的事情：

1. 不直接 import SDK 内部 AMap adapter。
2. 不在业务工程里拼 `McpGateway` 或 `AgentToolContext`。
3. 不实现真实地图 API 和最后 10 米导航策略；当前只接入红绿灯视觉事件的最小策略。
4. 不修改眼镜端或手机端 SDK 框架代码。

验证时启动真实服务端，并按需使用真实 iOS phone 与 `glass-playback` 设备级回放。
