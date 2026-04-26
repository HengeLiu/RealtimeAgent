# navigation

导航能力用于验证 `sdk-v3` 提供的业务上下文 MCP 调用入口，并把路线准备结果沉淀为 SDK 托管任务。

当前实现范围：

1. `prepare_navigation` Tool 校验目的地并调用 `context.mcp("amap.route_plan", ...)`。
2. `MockAmapMcpAdapter` 在业务工程内提供稳定的 AMap mock 路线规划结果。
3. `navigation_task` 保存路线、提交路线准备通知，并支持进展事件、到达事件和取消。
4. `ScenarioRunner` 场景回放验证 Tool、MCP trace、任务状态和通知。

当前不做的事情：

1. 不直接 import SDK 内部 AMap adapter。
2. 不在业务工程里拼 `McpGateway` 或 `AgentToolContext`。
3. 不实现真实地图 API、手机视觉协同和最后 10 米导航策略。
4. 不修改眼镜端或手机端 SDK 框架代码。

回放验证：

```bash
uv run python scripts/run_sdk_scenario.py --scenario testdata/scenario/navigation_prepare_basic.json --pretty
uv run python scripts/run_sdk_scenario.py --scenario testdata/scenario/navigation_missing_destination.json --pretty
```

