# for-blind-app

这是盲人眼镜业务示例的最小 app-root。当前只保留运行所需配置和能力代码：

| 路径 | 作用 |
| --- | --- |
| `server.yaml` | 应用运行配置。 |
| `capabilities/tools.py` | 应用级 Tool：`capture_photo`、`query_route_plan`、`search_web`。 |
| `capabilities/tasks.py` | 应用级 Task：`find_object_task`、`traffic_light_task`、`timer_task`。 |

启动方式：

```bash
uv run audio-chat.server.run --app-name for-blind-app
```

当前边界：

- 所有后台任务都通过 SDK 内置 `task_runtime_manager` 启动、查询和取消。
- 找物、红绿灯只保留 mock Task；YOLO 迁移完成前不引入端侧视觉任务实现。
- 地图和搜索没有配置 MCP 时返回明确 fallback。
- 图片、音频等媒体字节走 stream，业务代码只处理 `AssetRef`。
