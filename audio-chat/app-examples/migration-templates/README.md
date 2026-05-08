# audio-chat 业务迁移样板

本目录提供从旧 SDK 迁移业务能力时可以复制的最小样板。样板只依赖
`audio_chat` 顶层公开 API，并且只通过 `UserDeviceContext` 表达设备通讯。

## 样板清单

| 样板 | 适用场景 | 关键边界 |
| --- | --- | --- |
| `find_object/tool.py` | 一次性视觉 Tool，类似旧版找物或看前方 | 请求图片资产，真实图片字节走 `sensor.rgb` stream |
| `continuous_rgb_analyze/task.py` | 持续视觉 Task，类似找物、红绿灯或导航执行期视觉事件 | 用事件配置连续上传，再通过 `watch_assets()` 消费资产 |
| `notification_task/task.py` | 后台任务通知样板，类似计时器到点或状态提醒 | 只提交结构化文本输出，不直接操作播放器 |

## 迁移约束

1. 不允许硬编码 `device_id` 做点对点发送。
2. 不允许新增隐藏 RPC 或直接操作 WebSocket。
3. 大字节媒体必须走 `sensor.*` 或 `actuator.*` stream。
4. 控制事件 payload 只放语义、配置和关联 ID。
5. Tool / Task 需要设备通讯时，只通过 `context.devices` 使用 `UserDeviceContext`。

独立验收：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py next-docs-contract
```
