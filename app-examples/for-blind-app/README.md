# for-blind-app 迁移样板

本目录记录openaiglass-for-blind 五类业务能力迁移到 `audio-chat` 的开发者样板。当前已提供可执行 Tool / Task、MCP mock、playback 配置和 `device-api-upgrade-capabilities` lane；这些样板用于证明功能开发者能按新版 Context 能力 API、stream、Task Engine 和 Output Service 完成等价迁移，不代表已经接入真实地图、真实搜索、真实 iOS 模型或 ESP32 真机效果。

当前可用的 Context API、设备能力文件、AssetRef 和端侧注册流程统一见 [设备注册与功能开发说明](../../docs/device-capability-development-guide.md)。下一阶段目标 API 见 [Context 与设备 API 设计说明](../../docs/context-device-api-design.md)。本目录只说明盲人眼镜业务样板如何落到这些公开接口上。

## 目录职责

| 目录 | 迁移目标 | 当前支撑 |
| --- | --- | --- |
| `capabilities/capture_photo` | 当前画面抓拍 | App 级 `capture_photo` Tool，通过 `sensor.rgb.one()` 返回 AssetRef。 |
| `capabilities/find_object` | 找物 Task | `find_object_task`，当前为 RGB 抓拍 + mock 识别结果，YOLO 迁移完成后替换识别逻辑。 |
| `capabilities/traffic_light` | 红绿灯识别 Task | `traffic_light_task`，当前为 RGB 抓拍 + mock 灯色结果，YOLO 迁移完成后替换识别逻辑。 |
| `capabilities/navigation` | 路线规划查询 | `query_route_plan` Tool，调用 MCP mock/fallback，不启动后台导航任务。 |
| `capabilities/search` | 搜索和问答资料查询 | MCP wrapper Tool，缺真实 provider 时明确 mock/fallback。 |
| `capabilities/timer` | 计时器 Task | `timer_task`，通过统一 `task_runtime_manager` 启动、查询、取消。 |
| `host/server` | 业务 app factory | 真实代码读取 YAML 并启用自动发现。 |
| `host/phone-mock` | 视觉任务 mock 端 | 真实配置可复制 `device-examples/python-phone/phone.mock.yaml`。 |
| `host/glass-playback` | 设备级回放入口 | 真实配置可复制 `app-examples/for-blind-app/host/glass-playback/playback.yaml`。 |

## 开发顺序

1. 先跑基础样板：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py capability-template-playback \
  --report runs/acceptance/capability-template-playback.json
```

2. 复制 `app-examples/for-blind-app/templates` 中最接近的 Tool / Task。
3. 按本目录各能力 README 收敛 Context API、stream_type、AssetRef 和输出产物。
4. 运行独立回放配置和验收测试。
5. 修改后继续注册到 `scripts/acceptance_check.py device-api-upgrade-capabilities`。

验收命令：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py device-api-upgrade-capabilities \
  --report runs/acceptance/device-api-upgrade-capabilities.json
```

## 边界

- 不新增点对点 device RPC；当前获取画面使用 `await context.devices.sensors.rgb.one(...)`。
- 找物、红绿灯当前只保留 mock Task；YOLO 迁移完成前不保留端侧 phone task 专用实现。
- 所有后台任务都通过 SDK 内置 `task_runtime_manager` 启动、查询、取消，不再暴露 `start_*` 专用 Tool。
- 图片、视频、IMU、音频字节走 stream，并以 `AssetRef` 引用结果。
- 用户可听见的提示进入 Output Service。
- 外部地图、搜索和业务系统通过 MCP 或普通 provider 封装。
