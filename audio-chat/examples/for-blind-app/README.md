# for-blind-app 迁移样板

本目录记录旧 `openaiglass-for-blind` 五类业务能力迁移到 `audio-chat` 的开发者样板。当前已提供可执行 Tool / Task、MCP mock、playback 配置和 `old-sdk-parity-capabilities` lane；这些样板用于证明功能开发者能按 `UserDeviceContext`、event + stream、Asset Service、Task Engine 和 Output Service 完成等价迁移，不代表已经接入真实地图、真实搜索、真实 iOS 模型或 ESP32 真机效果。

## 目录职责

| 目录 | 迁移目标 | 当前支撑 |
| --- | --- | --- |
| `capabilities/find_object` | 找物或看前方 Tool / Task | 一次性 `sensor.rgb` 抓拍和持续视觉任务。 |
| `capabilities/traffic_light` | 红绿灯连续视觉识别 | 连续 `sensor.rgb` stream + TaskEvent + Output Service。 |
| `capabilities/navigation` | 路线准备和执行期导航 | MCP 路线准备 + 偏航、接近终点、视觉确认事件样板。 |
| `capabilities/search` | 搜索和问答资料查询 | MCP wrapper Tool，缺真实 provider 时明确 mock/fallback。 |
| `capabilities/timer` | 计时器和后台通知 | `TaskContext.schedule_event()` + 查询、取消、到点通知。 |
| `host/server` | 业务 app factory | 真实代码读取 YAML 并启用自动发现。 |
| `host/phone-mock` | 视觉任务 mock 端 | 真实配置可复制 `endpoints/python-phone-mock/phone.mock.yaml`。 |
| `host/glass-playback` | 设备级回放入口 | 真实配置可复制 `examples/basic-app/host/glass-playback/playback.yaml`。 |

## 开发顺序

1. 先跑基础样板：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py capability-template-playback \
  --report runs/acceptance/capability-template-playback.json
```

2. 复制 `examples/migration-templates` 中最接近的 Tool / Task。
3. 按本目录各能力 README 收敛事件名、stream_type、资产和输出产物。
4. 运行独立回放配置和验收测试。
5. 修改后继续注册到 `scripts/acceptance_check.py old-sdk-parity-capabilities`。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py old-sdk-parity-capabilities \
  --report runs/acceptance/old-sdk-parity-capabilities.json
```

## 边界

- 不新增 `start_phone_task`、`capture_photo` 或点对点 device RPC。
- 找物、红绿灯这类手机视觉能力只能由 `UserDeviceContext` 发布
  `control.device.command.requested`，端侧通过 `control.device.command.*` 回报；
  业务代码不得直接操作 phone 连接或导入内部 service。
- 图片、视频、IMU、音频字节走 stream 或 Asset Service。
- 用户可听见的提示进入 Output Service。
- 外部地图、搜索和业务系统通过 MCP 或普通 provider 封装。
