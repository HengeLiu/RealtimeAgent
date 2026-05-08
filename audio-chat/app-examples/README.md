# audio-chat app-examples

`app-examples/for-blind-app` 是当前推荐给开发者复制和启动的完整示例应用。它把旧 SDK 的找物、红绿灯、导航、搜索、计时器，以及通用抓拍、设备状态、连续视觉 stream、Task 和 MCP wrapper 都放在同一个 app-root 中。

其他目录只作为回归 fixture 或迁移模板保留：

| 目录 | 当前定位 |
| --- | --- |
| `for-blind-app` | 推荐开发入口。用于真实 server 启动、Tool / Task 自动发现、旧 SDK 能力迁移和设备级回放。 |
| `basic-app` | 兼容旧验收的最小 fixture。新的业务开发不要再从这里复制；其中可复用的抓拍、设备状态、stream 配置等能力已经下沉为 SDK built-in Tool。 |
| `minimal` | SDK 内部最小配置 fixture。用于协议、provider 和回放测试，不作为业务 app 样板。 |
| `migration-templates` | 单能力迁移模板。开发新能力时优先参考 `for-blind-app/capabilities`，只有需要独立复制某类模式时再看这里。 |

推荐启动方式：

```bash
cd audio-chat
uv run audio-chat.server.run --app-name for-blind-app
```

`--app-name for-blind-app` 会自动解析 `app-examples/for-blind-app`，加载根目录 `server.yaml`，并把 `capabilities` 目录加入 Tool / Task 自动发现。所有 app 的 `server.yaml` 都必须放在 app 根目录；如果 YAML 没有显式配置 `app_name`，SDK 会使用父目录名。

推荐验收：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py old-sdk-parity-capabilities \
  --report runs/acceptance/old-sdk-parity-capabilities.json
```
