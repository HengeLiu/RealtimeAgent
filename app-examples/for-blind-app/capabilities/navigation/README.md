# navigation 迁移样板

能力价值：准备目的地并查询路线规划。执行期导航 Task 暂不保留，后续接入真实定位和导航状态后再实现。

audio-chat 迁移路径：

1. `query_route_plan` Tool 调用 MCP 或 provider 做 POI、地理编码和路线准备。
2. Tool 不启动后台任务，不直接处理设备连接。
3. 未来执行期导航应作为 Task，由 `task_runtime_manager` 启动。

参考：

- `docs/phase3-migration-guide.md` 的 MCP Adapter 迁移章节。
- `docs/context-device-api-design.md` 的 Tool / Task 边界说明。

验收要求：

- mock 路线准备不需要真实地图 key。
- 配置真实地图 key 时，provider 错误要结构化记录，不能伪装成功。
- 执行期导航未实现前，不保留专用启动 Tool 或执行期 Task 样板。
