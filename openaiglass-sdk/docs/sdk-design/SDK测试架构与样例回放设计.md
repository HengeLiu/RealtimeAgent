# 设备级数据回放测试工具设计文档

## 1. 文档定位

本文档定义 SDK 后续唯一正式支持的高频业务自测方式：设备级数据回放。

设备级数据回放不是组件级测试，也不是离线调用某个 `Tool`、`Task`、`PhoneProcessor` 或业务 handler。它通过一个独立启动的 `glass-playback` Python 虚拟眼镜设备，连接真实服务端，按真实眼镜协议完成注册、绑定、心跳、语音会话、音频流发送、控制消息接收和执行器处理。

功能开发人员使用 `glass-playback` 的方式应与使用真实 ESP32 眼镜一致。差异只在于这台设备的麦克风、摄像头、传感器和执行器行为来自预先准备好的配置和数据资产。

当前只支持 `glass-playback`。需要手机能力时，回放链路仍使用真实 iOS phone。

## 2. 设计目标

1. 功能开发人员可以在没有真实眼镜硬件的情况下，用同一套 SDK 服务端和同一套设备注册流程完成业务自测。
2. `glass-playback` 在开发者视角里是一台独立设备，而不是测试 runner 内部的 mock 对象。
3. 服务端、业务 SDK、设备绑定、语音链路、Tool、Task、通知、控制协议全部走真实实现。
4. 每次回放都必须由配置中的触发音频驱动，模拟“眼镜端唤醒成功后开始录音并向服务端推流”的过程。
5. 开发者通过 actuator 输出、服务端日志、真实 iOS 日志和运行态接口自行判断结果。

## 3. 非目标

1. 不支持组件级场景回放。
2. 不支持 `ScenarioRunner` 式的 manifest 执行。
3. 不支持业务断言检查。
4. 不支持批量测试。
5. 不支持绕过真实服务端直接调用业务模块。
6. 不设计手机虚拟设备；需要手机能力时使用真实 iOS phone。
7. 不测试 WakeNet 本身；触发音频表示唤醒已经成功之后的麦克风录音流。

## 4. 核心概念

| 概念 | 定义 |
| --- | --- |
| `glass-playback` | 独立 Python 进程，实现真实 glass 设备协议，用配置和数据资产替代硬件输入输出。 |
| 触发音频 | 配置必填项，表示唤醒成功后的一次用户语音请求，会在设备注册、绑定和语音会话准备完成后自动流式发送。 |
| sensor asset | 麦克风、摄像头、视频帧、方向角等虚拟眼镜输入数据。 |
| actuator strategy | 虚拟眼镜收到服务端控制命令后的处理策略，例如记录、保存音频、自动回传播放完成。 |
| device group ready | 服务端已接受 glass 注册；如果本次能力需要手机，则真实 iOS phone 已注册并与 glass 完成绑定。 |

## 5. 总体架构

```text
openaiglass-for-blind/host/glass-playback/config/*.json
        |
        v
glass-playback process
  - playback config loader
  - control websocket client
  - audio stream client
  - sensor asset provider
  - actuator recorder
        |
        | /ws/control, /ws_audio
        v
real SDK server
  - device registration
  - device binding
  - voice runtime
  - agent-core
  - backend-task-core
  - business Tool / Task
        |
        | real control messages / video link / task events
        v
real iOS phone, when required
```

工具不在服务端进程内部创建 mock 设备。`glass-playback` 必须像真实眼镜一样单独启动、单独注册、单独保持心跳，并通过服务端公开协议参与运行时。

## 6. 启动与运行时序

一次回放的标准时序如下：

1. 开发者启动真实业务服务端。
2. 如果业务能力依赖手机，开发者启动真实 iOS phone。
3. 开发者启动 `glass-playback --config <playback.json>`。
4. `glass-playback` 读取配置，校验 `device_id`、`pair_token`、`control_ws_url`、`trigger_audio` 和资产路径。
5. `glass-playback` 连接服务端 `/ws/control`。
6. `glass-playback` 发送 `device.register(device_type=glass)`。
7. 服务端返回 `device.registered`。
8. `glass-playback` 开始心跳。
9. 如果配置要求等待绑定，`glass-playback` 等待服务端 runtime 中 glass 与真实 iOS phone 形成设备组绑定。
10. `glass-playback` 等待 `voice.session.open`，并回传 `voice.session.opened`。
11. `glass-playback` 自动读取 `trigger_audio.path`，按 `chunk_ms` 切片，通过 `/ws_audio` 流式发送 `MediaFrame(audio_chunk)`。
12. 服务端按真实语音链路完成 ASR、agent 调度、Tool 调用、Task 推进和通知下发。
13. `glass-playback` 接收服务端发给 glass 的控制消息，并按 actuator strategy 记录、保存或自动回执。
14. 开发者查看 actuator 输出、日志和 runtime snapshot，判断本次业务行为是否符合预期。

退出码只表示 `glass-playback` 工具本身是否启动、连接、读配置和传输成功，不代表业务结果通过或失败。

## 7. 状态机

`glass-playback` 建议实现以下状态：

| 状态 | 进入条件 | 退出条件 |
| --- | --- | --- |
| `loaded` | 配置和资产校验完成。 | 开始连接控制 WebSocket。 |
| `connected` | `/ws/control` 已连接。 | `device.register` 已发送。 |
| `registered` | 收到 `device.registered`。 | 心跳启动，并按配置等待绑定或语音会话。 |
| `bound` | 服务端 runtime 显示设备组已就绪；不需要 phone 时可跳过。 | 收到或发起 voice session ready 流程。 |
| `voice_ready` | 收到 `voice.session.open` 并确认会话打开。 | 开始发送触发音频。 |
| `streaming_audio` | 正在发送 `trigger_audio`。 | 音频发送完成或传输失败。 |
| `running` | 触发音频已发送，等待后续控制命令和执行器输出。 | 用户中断、超时或服务端断开。 |
| `stopped` | 工具正常结束。 | 无。 |
| `failed` | 配置错误、连接失败、协议错误或资产读取失败。 | 无。 |

## 8. 配置文件设计

配置文件描述一台虚拟眼镜的设备身份、输入数据和执行器行为。它不是业务测试脚本，不包含 `expected` 断言。

示例：

```json
{
  "device_type": "glass",
  "device_id": "glass-playback-001",
  "pair_token": "pair_playback",
  "control_ws_url": "ws://127.0.0.1:8765/ws/control",
  "audio_ws_url": "ws://127.0.0.1:8765/ws_audio",
  "desired_phone_device_id": "phone-001",
  "startup": {
    "wait_for_registration": true,
    "wait_for_binding": true,
    "wait_for_voice_session": true,
    "auto_stream_trigger_audio": true,
    "startup_timeout_ms": 30000
  },
  "sensors": {
    "trigger_audio": {
      "path": "testdata/audio/find_water_cup_trigger.wav",
      "format": "wav",
      "sample_rate_hz": 16000,
      "channels": 1,
      "chunk_ms": 40
    },
    "camera_capture": {
      "path": "testdata/image/cup.jpg",
      "mime_type": "image/jpeg"
    },
    "camera_stream": {
      "path": "testdata/video/find_object_water_cup.mp4",
      "codec": "mp4",
      "frame_interval_ms": 100
    },
    "heading": {
      "path": "testdata/sensor/find_object_heading.json"
    }
  },
  "actuators": {
    "audio_play": {
      "mode": "record_and_auto_finish",
      "save_audio_to": "runs/playback/glass-playback-001/audio"
    },
    "vibrate": {
      "mode": "record"
    }
  },
  "outputs": {
    "event_log": "runs/playback/glass-playback-001/events.jsonl",
    "actuator_log": "runs/playback/glass-playback-001/actuators.jsonl"
  }
}
```

必填字段：

1. `device_type`：当前固定为 `glass`。
2. `device_id`：必须与服务端 `device_token_map` 中的设备编号匹配。
3. `pair_token`：必须与服务端 `device_token_map` 中的令牌匹配。
4. `control_ws_url`：真实服务端控制通道地址。
5. `sensors.trigger_audio`：必填触发音频配置。

可选字段：

1. `audio_ws_url`：未配置时由 `control_ws_url` 推导。
2. `desired_phone_device_id`：仅业务能力需要真实 iOS phone 时配置。
3. `camera_capture`、`camera_stream`、`heading` 等传感器输入：按业务需要配置。
4. `outputs`：用于保存工具日志和 actuator 输出。

## 9. 触发音频要求

`trigger_audio` 是每次回放的触发源，必须满足：

1. 包含完整用户请求，例如“帮我找一下水杯”，不是单独的唤醒词。
2. 格式优先使用 16 kHz、单声道 WAV。
3. 文件路径必须在启动前存在。
4. 发送前必须完成 glass 注册。
5. 如果配置需要手机，发送前必须完成真实 iOS phone 与 glass 的绑定。
6. 发送前必须确认 voice session 已打开。
7. 发送方式必须是流式音频帧，而不是一次性把文件内容作为业务输入传给服务端。

这段音频不用于验证唤醒算法。它只模拟唤醒成功后，眼镜麦克风持续把用户语音推给服务端。

## 10. 传感器资产

传感器资产只表达虚拟眼镜能读到的数据，不表达测试期望。

推荐目录：

```text
openaiglass-for-blind/
  host/
    glass-playback/
      config/
testdata/
  audio/
  image/
  video/
  sensor/
  text/
```

`host/glass-playback/config` 存放 `glass-playback` 设备配置。`testdata` 只存放可复用数据资产，例如音频、图片、视频、传感器和文本样例。

`camera_stream` 应模拟真实摄像头视频输入，优先使用 MP4：

```json
{
  "path": "testdata/video/find_object_water_cup.mp4",
  "codec": "mp4",
  "frame_interval_ms": 100
}
```

需要逐帧控制时，可以使用图片帧序列：

```json
{
  "frames": [
    {
      "path": "image/cup-001.jpg",
      "codec": "jpeg",
      "t_ms": 0
    },
    {
      "path": "image/cup-002.jpg",
      "codec": "jpeg",
      "t_ms": 100
    }
  ]
}
```

方向传感器示例：

```json
{
  "readings": [
    { "t_ms": 0, "heading_deg": 70.0 },
    { "t_ms": 500, "heading_deg": 73.5 },
    { "t_ms": 1000, "heading_deg": 76.0 }
  ]
}
```

## 11. 执行器策略

`glass-playback` 需要支持最小执行器策略，帮助开发者观察服务端是否下发了预期命令。

| 执行器 | 策略 | 行为 |
| --- | --- | --- |
| `audio_play` | `record` | 只记录播放请求和元数据。 |
| `audio_play` | `record_and_auto_finish` | 记录请求，保存音频流，并自动回传播放 started/finished。 |
| `vibrate` | `record` | 记录震动命令。 |
| `display` | `record` | 如果未来 glass 协议出现显示类命令，只记录命令。 |

执行器输出是开发者判断结果的主要依据之一，但 SDK 当前不提供断言检查。

## 12. 日志与输出

建议输出两类 JSONL 文件：

1. `events.jsonl`：记录注册、心跳、绑定、语音会话、音频发送进度、控制消息和错误。
2. `actuators.jsonl`：记录服务端下发给 glass 的执行器命令和本地处理结果。

示例：

```json
{"ts":"2026-04-27T10:00:00Z","type":"device.registered","device_id":"glass-playback-001"}
{"ts":"2026-04-27T10:00:01Z","type":"voice.trigger_audio.started","path":"testdata/audio/find_water_cup_trigger.wav"}
{"ts":"2026-04-27T10:00:04Z","type":"actuator.audio_play","mode":"record_and_auto_finish","saved_to":"runs/playback/glass-playback-001/audio/0001.wav"}
```

这些日志只提供事实记录，不产生业务通过或失败结论。

## 13. SDK 模块划分

建议将设备级回放能力作为 SDK devtools 能力实现，业务工程只保留薄启动脚本。

推荐模块：

| 模块 | 职责 |
| --- | --- |
| `openaiglasses.playback.config` | 解析和校验 `glass-playback` 配置。 |
| `openaiglasses.playback.assets` | 读取音频、图片、文本帧和传感器资产。 |
| `openaiglasses.playback.glass_device` | 实现虚拟 glass 设备状态机。 |
| `openaiglasses.playback.control_client` | 连接 `/ws/control`，处理注册、心跳、控制消息和回执。 |
| `openaiglasses.playback.audio_stream` | 将 `trigger_audio` 切片并通过 `/ws_audio` 流式发送。 |
| `openaiglasses.playback.actuators` | 执行器策略、日志和音频保存。 |
| `openaiglasses.playback.cli` | 提供 `run_playback_glass` 命令入口。 |

业务工程中的 `scripts/run_playback_glass.py` 只负责把本地路径、环境变量和配置文件传给 SDK CLI。

## 14. 错误处理

工具错误分为三类：

| 类型 | 示例 | 处理 |
| --- | --- | --- |
| 配置错误 | 缺少 `trigger_audio`、资产不存在、`device_type` 不是 `glass`。 | 启动前失败，退出码非 0。 |
| 连接错误 | 服务端不可达、注册失败、认证失败。 | 写入 `events.jsonl`，退出码非 0。 |
| 运行时错误 | 音频发送中断、控制消息无法识别、执行器保存失败。 | 写入 `events.jsonl`；严重错误退出码非 0。 |

业务结果不符合预期不属于工具错误，由开发者根据输出自行判断。

## 15. 与真机联调的关系

设备级回放用于降低业务开发期间的眼镜硬件依赖，但不能替代全部真机验收。

设备级回放适合验证：

1. 服务端能否接收真实协议设备注册。
2. 语音触发后能否进入正确业务能力。
3. Task 和通知是否通过真实运行时推进。
4. 手机依赖缺失、视频链路失败、取消路径等错误处理是否合理。
5. 服务端是否向 glass 下发预期执行器命令。

真机仍必须验证：

1. WakeNet 唤醒质量。
2. 真实麦克风采集质量。
3. 扬声器播放质量。
4. 真实 ESP32 网络稳定性。
5. 真实硬件按钮、震动和传感器误差。
6. 端到端延迟、功耗和发热。

## 16. 验收标准

设备级数据回放工具完成后，至少满足：

1. 可以用 `glass-playback` 独立进程连接真实服务端并完成 glass 注册。
2. `device_id` 和 `pair_token` 使用方式与真机完全一致。
3. 可以等待真实 iOS phone 绑定后再发送触发音频。
4. 可以在无需 phone 的能力中只等待 glass 注册和 voice session。
5. 每份配置必须包含 `trigger_audio`，缺失时启动失败。
6. 触发音频通过 `/ws_audio` 以音频帧流式发送。
7. 可以记录服务端下发的 audio play 和 vibrate 命令。
8. 可以保存服务端下发的播放音频流。
9. 不提供 `expected` 字段、不做断言、不做批量运行。
10. SDK 指南、测试文档和业务 README 不再引导开发者使用组件级场景回放。
