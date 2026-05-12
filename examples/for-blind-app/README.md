# for-blind-app

这是盲人眼镜业务示例的最小 app-root。当前只保留运行所需配置和能力代码：

| 路径 | 作用 |
| --- | --- |
| `server.yaml` | 应用运行配置。 |
| `capabilities/tools.py` | 应用级 Tool：`capture_photo`、`interpret_image`、`interpret_current_view`、`query_route_plan`、`search_web`。其中三个图片相关 Tool 当前通过 `tools.denylist` 暂不暴露给模型。 |
| `capabilities/tasks.py` | 应用级 Task：`find_object_task`、`traffic_light_task`、`timer_task`。 |

## 路径配置

`server.yaml` 默认把运行产物放在当前应用目录下：

```text
examples/for-blind-app/audio-server/runs/
```

日常不需要分别配置用户消息、资产、记忆、任务和 preflight 报告路径。需要把所有运行产物迁移到其它目录时，只配置一个入口：

```yaml
paths:
  runtime_root: "/tmp/for-blind-app-runs"
```

未显式配置时，SDK 会自动派生：

| 配置项 | 默认值 |
| --- | --- |
| `observability.runs_root` | `<runtime_root>` |
| `user.message_store.root` | `<runtime_root>/users` |
| `asset.root` | `<runtime_root>/assets` |
| `memory.path` | `<runtime_root>`，实际文件为 `<runtime_root>/<user_id>/memory.json` |
| `dev_checks.report_path` | `<runtime_root>/preflight.json` |

启动方式：

```bash
uv run audio-chat.server.run --app-name for-blind-app
```

当前边界：

- 后台任务由 SDK 自动生成的 `start_*_task` Tool 启动，`task_runtime_manager` 负责查询、取消和列表。
- 找物、红绿灯只保留 mock Task；YOLO 迁移完成前不引入端侧视觉任务实现。
- 地图和搜索没有配置 MCP 时返回明确 fallback。
- 图片、音频等媒体字节走 stream，业务代码只处理 `AssetRef`。
