# 端侧协议盘点和冻结候选

更新时间：2026-05-15

当前状态：本盘点已经用于 `audio-device/` 首批 SDK 实现。当前协议仍以结构化
`supports`、`command.*`、`stream.control.open.requested` 和
`stream.control.close.requested` 为准；端侧注册 payload 不允许手写 `routes`。

## 1. 盘点范围

本文件记录多语言端侧通讯 SDK 第一轮实现前的真实协议现状。结论来自当前代码和参考端，不从设计预期反推。

涉及文件：

| 范围 | 文件 |
| --- | --- |
| 协议常量和事件信封 | `audio-server/realtime_agent/protocol.py` |
| 设备注册和路由 | `audio-server/realtime_agent/control/service.py` |
| 控制 / stream WebSocket | `audio-server/realtime_agent/server.py` |
| stream 生命周期 | `audio-server/realtime_agent/stream/service.py` |
| 能力声明校验 | `audio-server/realtime_agent/device_capabilities.py` |
| Python 回放端 | `examples/dev-support/devices/python-playback-glass/realtime_agent_python_playback_glass/protocol_client.py` |
| 浏览器眼镜模拟组件 | `examples/dev-support/devices/browser-glass/index.html` |
| iOS 参考端 | `examples/for-blind-app/devices/native-ios-phone/RealtimeAgentPhone/Core/` |
| ESP32-S3 参考端 | `examples/for-blind-app/devices/native-esp32-glass/firmware/main/realtime_agent_reference_main.c` |

## 2. 控制事件清单

第一版多语言 Device SDK 需要类型化支持以下事件：

| 事件 | 方向 | 用途 |
| --- | --- | --- |
| `control.device.register.requested` | device -> server | 设备注册。 |
| `control.device.registered` | server -> device | 注册成功。 |
| `control.device.register.failed` | server -> device | 注册失败。 |
| `control.device.heartbeat.received` | device -> server | 端侧心跳。 |
| `control.device.state.changed` | server -> device / runs | 设备连接状态变化。 |
| `command.requested` | server -> device | 请求端侧执行命令。 |
| `command.accepted` | device -> server | 端侧接受命令。 |
| `command.progress` | device -> server | 端侧回报命令进度。 |
| `command.completed` | device -> server | 端侧回报命令完成。 |
| `command.failed` | device -> server | 端侧回报命令失败。 |
| `stream.control.open.requested` | server -> device | 请求打开普通传感器 stream。 |
| `stream.control.close.requested` | server -> device | 请求关闭普通传感器 stream。 |
| `stream.input.opened` | device -> server | 输入 stream 已打开。 |
| `stream.input.closed` | device -> server / server -> device | 输入 stream 已关闭。 |
| `stream.input.failed` | device -> server / server -> device | 输入 stream 失败。 |
| `stream.output.open.requested` | server -> device | 请求打开输出 stream。 |
| `stream.output.close.requested` | server -> device | 请求关闭输出 stream。 |
| `stream.output.started` | device -> server | 输出播放开始。 |
| `stream.output.finished` | device -> server | 输出播放完成。 |
| `stream.output.closed` | device -> server | 输出播放关闭。 |
| `stream.output.failed` | device -> server / server -> device | 输出 stream 失败。 |
| `stream.output.cancel.requested` | server -> device | 请求取消输出 stream。 |
| `stream.output.cancelled` | device -> server / server -> device | 输出 stream 已取消。 |

`control.audio_session.*`、`control.user.*`、`agent.*`、`tool.*`、`task.*` 和 `system.*` 事件仍属于 server 主链路或内部观测事件。Python 基准 SDK 可以收发这些事件，但第一版跨语言 SDK 不把它们作为端侧能力 API 的主路径。

## 3. 注册 payload 字段

设备注册事件必须满足：

```json
{
  "event_name": "control.device.register.requested",
  "user_id": "user-001",
  "producer_id": "dev-001",
  "payload": {
    "device_id": "dev-001",
    "name": "Device",
    "client_type": "python",
    "sdk_version": "0.1.0",
    "runtime": {
      "platform": "python",
      "language": "python"
    },
    "properties": {},
    "supports": {
      "sensors": [],
      "actuators": []
    }
  }
}
```

实际约束：

- `producer_id` 必须等于 `payload.device_id`。
- `version` 必须是 `realtime-agent.v1`。
- `payload.supports` 必须是结构化 `sensors/actuators`。
- 注册 payload 不能包含 `routes`，server 会从 `supports` 和 `properties` 编译内部路由。
- 注册 payload 不能包含旧 `capabilities` 字段。

## 4. supports 能力清单

公开结构化能力：

| supports 写法 | 内部能力 ID | 内部路由 |
| --- | --- | --- |
| `sensors[].type=rgb` | `sensor.rgb` | `stream.control.*` + `stream_type=sensor.rgb` |
| `sensors[].type=imu` | `sensor.imu` | `stream.control.*` + `stream_type=sensor.imu` |
| `sensors[].type=tof` | `sensor.tof` | `stream.control.*` + `stream_type=sensor.tof` |
| `actuators[].type=vibrator` | `actuator.haptic` | `stream.output.*` + `stream_type=actuator.haptic`，以及 `command.*` |
| `actuators[].type=haptic` | `actuator.haptic` | 同上 |

系统音频不属于普通 `supports`：

- `sensor.mic` 通过 `properties.realtime_agent.audio_input=sensor.mic` 进入系统音频链路。
- `actuator.speaker` 通过 `properties.realtime_agent.audio_output=actuator.speaker` 进入输出播放链路。

## 5. stream header 字段

当前二进制帧格式：

```text
4 bytes big-endian header length
header JSON bytes
payload bytes
```

header 字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `version` | 是 | `realtime-agent.v1`。 |
| `user_id` | 是 | 用户 ID。 |
| `session_id` | 是 | 当前实现常用设备 ID 或会话 ID。 |
| `stream_id` | 是 | stream ID。 |
| `stream_type` | 是 | `sensor.rgb`、`sensor.mic`、`actuator.speaker` 等。 |
| `seq` | 是 | chunk 顺序号。 |
| `timestamp_ms` | 是 | 端侧发送时间。 |
| `codec` | 是 | `pcm16le`、`jpeg`、`png` 等。 |
| `sample_rate` | 条件 | 音频必填；图片当前可用 `1`。 |
| `channels` | 条件 | 音频必填；图片当前可用 `1`。 |
| `duration_ms` | 条件 | 音频 chunk 时长；图片可为 `0`。 |
| `payload_size` | 是 | payload 字节数，必须和实际长度一致。 |
| `final` | 是 | 是否最后一帧。 |
| `metadata` | 否 | request_id、source_path 等调试字段。 |

## 6. 开发支持组件和参考端差异

| 组件 / 参考端 | 现状 | 第一轮处理 |
| --- | --- | --- |
| Python playback glass / Python phone 开发支持组件共享网络基类 | 已通过真实 `/ws/control` 和 `/ws/stream` 回放；原本自带 URL、注册事件和 stream chunk 编解码。 | 已切到 `realtime_agent_device.RealtimeAgentDeviceClient`、`RealtimeAgentEvent`、`ws_url` 和 `StreamChunkCodec`。 |
| browser-glass 开发支持组件 | HTML 内保留交互式 UI 和业务事件处理，但底层通讯对象已迁移。 | 已通过本地 adapter re-export TypeScript SDK，并复用 `RealtimeAgentDeviceClient`、`RealtimeAgentEvent`、`DeviceBuilder` 和 `StreamChunkCodec`。 |
| iOS Swift phone | `RealtimeAgentEvent.swift` 和 `StreamChunkCodec.swift` 自带协议实现。 | 后续 Swift Package 阶段迁移。 |
| ESP32-S3 | 当前是可构建骨架，协议清单写在 C 注释中。 | 后续 C SDK 阶段迁移 header 和 codec。 |

## 7. 必须统一项

1. 事件名清单进入 `realtime-agent-event.schema.json`。
2. stream header 字段进入 `realtime-agent-stream.schema.json`。
3. 端侧错误码进入 `realtime-agent-error-codes.yaml`。
4. WebSocket 通道进入 `realtime-agent-asyncapi.yaml`。
5. 黄金样例进入 `protocol/data/fixtures/`。
6. Python 参考端优先复用基准 SDK 的连接、事件和 stream chunk codec。

## 8. 可后置项

1. iOS 参考端切 Swift Package。
2. Kotlin / Java Android SDK。
3. C SDK 的 ESP-IDF component。
4. NuGet、crates.io、pub.dev 等 P1 语言发布。

## 9. 第一版 SDK 支持边界

第一版 SDK 支持：

- 设备注册。
- 心跳。
- 控制事件收发。
- 命令请求和回执。
- `stream.control.open.requested` 到输入 stream 上传。
- 输出 stream chunk 接收。
- stream chunk 编解码。
- 诊断快照。

第一版 SDK 暂不承诺：

- 真机相机、麦克风、扬声器驱动。
- 生产鉴权 token 刷新。
- 自动发布到各语言包仓库。
- TypeScript、Swift、Kotlin、C 的完整实现。
