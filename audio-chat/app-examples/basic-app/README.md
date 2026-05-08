# audio-chat basic-app

这是给功能开发者复制的最小 app-root 示例。P0-A 只冻结最小开发入口：

1. `capabilities/sample_tool/tool.py`：一个可被自动发现的 Tool 样板。
2. `capabilities/sample_task/task.py`：一个可被自动发现的 Task 样板。
3. `host/server/main.py`：一个最小 app 创建入口。

## 本地回放

```bash
cd audio-chat
uv run python scripts/acceptance_check.py capability-template-playback \
  --report runs/acceptance/capability-template-playback.json
```

`capability-template-playback` 属于后续 P0-B 线路；P0-A 只要求设备级
playback 验收入口存在。

## 启动示例 server

```bash
cd audio-chat
uv run audio-chat.server.run --app-name basic-app
```

`--app-name basic-app` 会自动解析 `app-examples/basic-app`，加载根目录 `server.yaml`，并发现 `capabilities` 下的 Tool / Task。
