# audio-chat basic-app

这是 SDK 级最小应用，用于配置加载、Tool / Task 自动发现、设备级回放、Text Agent 和 Realtime Agent 基础验收。业务能力开发优先从 `for-blind-app` 开始；需要验证 SDK 扩展面的最小闭环时使用本目录。

1. `capabilities/sample_tool/tool.py`：一个可被自动发现的 Tool 样板。
2. `capabilities/sample_task/task.py`：一个可被自动发现的 Task 样板。
3. `server.yaml`：完整最小配置，覆盖控制、stream、asset、agent、output、tool、task、观测和开发检查。
4. `server-omni.yaml`：Realtime Audio Agent 的最小启动配置。
5. `host/glass-playback/sdk-playback.yaml`：SDK 基础回放配置。

## 本地回放

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py capability-template-playback \
  --report runs/acceptance/capability-template-playback.json
```

## 启动示例 server

```bash
# 在项目根目录执行
uv run audio-chat.server.run --app-name basic-app
```

`--app-name basic-app` 会自动解析 `app-examples/basic-app`，加载根目录 `server.yaml`，并发现 `capabilities` 下的 Tool / Task。
