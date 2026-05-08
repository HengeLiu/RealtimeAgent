# audio-chat app-examples

`app-examples/for-blind-app` 是当前推荐给业务开发者启动和扩展的完整示例应用。它把旧 SDK 的找物、红绿灯、导航、搜索、计时器，以及通用抓拍、设备状态、连续视觉 stream、Task 和 MCP wrapper 都放在同一个 app-root 中。

`app-examples/basic-app` 是 SDK 级最小应用，用于配置、自动发现、回放和 provider 链路验收。它同时承载基础 Text Agent 与 Realtime Agent 启动配置。

| 目录 | 当前定位 |
| --- | --- |
| `for-blind-app` | 推荐开发入口。用于真实 server 启动、Tool / Task 自动发现、旧 SDK 能力迁移和设备级回放。 |
| `basic-app` | SDK 最小应用。用于配置加载、Tool / Task 自动发现、设备级回放、Text Agent 和 Realtime Agent 基础验收。 |
| `for-blind-app/templates` | 业务能力模板。用于参考一次性视觉 Tool、连续视觉 Task 和后台通知 Task 的公开 API 写法，不参与自动发现。 |

推荐启动方式：

```bash
# 在项目根目录执行
uv run audio-chat.server.run --app-name for-blind-app
```

`--app-name for-blind-app` 会自动解析 `app-examples/for-blind-app`，加载根目录 `server.yaml`，并把 `capabilities` 目录加入 Tool / Task 自动发现。所有 app 的 `server.yaml` 都必须放在 app 根目录；如果 YAML 没有显式配置 `app_name`，SDK 会使用父目录名。

推荐验收：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py old-sdk-parity-capabilities \
  --report runs/acceptance/old-sdk-parity-capabilities.json
```
