# audio-chat examples

`examples/for-blind-app` 是当前唯一推荐给业务开发者启动和扩展的完整示例应用。它把找物、红绿灯、导航、搜索、计时器，以及 SDK 级抓拍、设备状态、连续视觉 stream、Task、MCP wrapper、Text Agent 和 Realtime Agent 验收入口都放在同一个 app-root 中。

| 目录 | 当前定位 |
| --- | --- |
| `for-blind-app` | 唯一应用示例。用于真实 server 启动、Tool / Task 自动发现、SDK 基础能力和设备级回放。 |
| `dev-support` | 浏览器、Python phone、Python glass 等本地参考端与契约测试辅助实现。 |

推荐启动方式：

```bash
# 在项目根目录执行
uv run audio-chat.server.run --app-name for-blind-app
```

`--app-name for-blind-app` 会自动解析 `examples/for-blind-app/audio-server`，加载其中的 `server.yaml`，并把同级 `capabilities` 目录加入 Tool / Task 自动发现。所有 app 的 `server.yaml` 都必须放在 app 的 `audio-server` 根目录；如果 YAML 没有显式配置 `app_name`，SDK 会使用应用目录名。

推荐验收：

```bash
# 在项目根目录执行
uv run audio-chat.dev.preflight \
  --config examples/for-blind-app/audio-server/server.yaml \
  --report runs/acceptance/preflight.json

uv run python -m pytest examples/for-blind-app/tests examples/dev-support/tests -q
```
