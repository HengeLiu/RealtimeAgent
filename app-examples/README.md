# audio-chat app-examples

`app-examples/for-blind-app` 是当前唯一推荐给业务开发者启动和扩展的完整示例应用。它把找物、红绿灯、导航、搜索、计时器，以及 SDK 级抓拍、设备状态、连续视觉 stream、Task、MCP wrapper、Text Agent 和 Realtime Agent 验收入口都放在同一个 app-root 中。

| 目录 | 当前定位 |
| --- | --- |
| `for-blind-app` | 唯一应用示例。用于真实 server 启动、Tool / Task 自动发现、SDK 基础能力和设备级回放。 |
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
uv run python scripts/acceptance_check.py device-api-upgrade-capabilities \
  --report runs/acceptance/device-api-upgrade-capabilities.json
```
