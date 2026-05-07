# for-blind-app 迁移样板

本目录记录旧 `openaiglass-for-blind` 五类业务能力迁移到 `audio-chat` 的开发者样板。它是文档和目录样板，不声称五类能力都已经完成端到端实现；真正成功路径由后续 `old-sdk-parity-capabilities` lane 补代码和设备级回放。

## 目录职责

| 目录 | 迁移目标 | 当前支撑 |
| --- | --- | --- |
| `capabilities/find_object` | 找物或看前方 Tool / Task | 一次性 `sensor.rgb` 资产请求样板。 |
| `capabilities/traffic_light` | 红绿灯连续视觉识别 | 连续 `sensor.rgb` stream + TaskEvent 样板。 |
| `capabilities/navigation` | 路线准备和执行期导航 | MCP 路线准备 + location / heading / visual event 样板。 |
| `capabilities/search` | 搜索和问答资料查询 | MCP wrapper Tool 样板。 |
| `capabilities/timer` | 计时器和后台通知 | Task 调度 + Output Service 样板。 |
| `host/server` | 业务 app factory | 真实代码可复制 `examples/basic-app/host/server/main.py`。 |
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
4. 为能力补独立回放配置和验收测试。
5. 注册到 `scripts/acceptance_check.py old-sdk-parity-capabilities`。

## 边界

- 不新增 `start_phone_task`、`capture_photo` 或点对点 device RPC。
- 图片、视频、IMU、音频字节走 stream 或 Asset Service。
- 用户可听见的提示进入 Output Service。
- 外部地图、搜索和业务系统通过 MCP 或普通 provider 封装。
