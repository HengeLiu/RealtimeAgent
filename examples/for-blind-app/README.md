# for-blind-app

这是盲人眼镜业务示例的最小 app-root。当前只保留运行所需配置和能力代码：

| 路径 | 作用 |
| --- | --- |
| `server.yaml` | 应用运行配置。 |
| `capabilities/tools.py` | 应用级 Tool：`capture_photo`、`interpret_image`、`interpret_current_view`、`query_route_plan`、`search_web`。其中三个图片相关 Tool 当前通过 `tools.denylist` 暂不暴露给模型。 |
| `agent-server/mcp.yaml` | Amap MCP 配置，通过 `AMAP_MCP_URL` 和 `AMAP_MCP_BEARER_TOKEN` 连接远程 MCP。 |
| `agent-server/mcp.example.yaml` | Amap MCP 示例配置，用于对照远程 MCP 配置格式。 |
| `capabilities/tasks.py` | 应用级 Task：`find_object_task`、`traffic_light_task`、`timer_task`。 |

## 路径配置

`server.yaml` 默认把运行产物放在当前应用目录下：

```text
examples/for-blind-app/agent-server/runs/
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
uv run realtime-agent.server.run --app-name for-blind-app
```

当前边界：

- 后台任务由 SDK 自动生成的 `start_*_task` Tool 启动，`task_runtime_manager` 负责查询、取消和列表。
- 找物、红绿灯通过 peer video 在 Python phone 端处理；跨端视频连接见 [跨端设备直连视频任务设计](docs/devices/peer-video-link-task-design.md)。
- 搜索使用 Bocha Web Search API。需要设置 `BOCHA_SEARCH_API_KEY`，可用 `BOCHA_SEARCH_API_URL` 覆盖默认地址 `https://api.bochaai.com/v1/web-search`。
- 路线规划使用 Amap MCP。默认读取 `agent-server/mcp.yaml`，通过 `AMAP_MCP_URL` 连接摩搭等远程 Streamable HTTP MCP，通过 `AMAP_MCP_BEARER_TOKEN` 做远程服务鉴权。高德 Web 服务 API Key 应配置在远程 MCP 的部署环境中。
- 如果不想依赖启动 shell 的 `export`，可以把 `AMAP_MCP_URL` 和 `AMAP_MCP_BEARER_TOKEN` 写入 `agent-server/mcp.local.env`；该文件只用于本地运行，不提交仓库。
- 地图或搜索未配置时返回明确 fallback，不伪装成真实结果。
- 图片、音频等媒体字节走 stream，业务代码只处理 `AssetRef`。
