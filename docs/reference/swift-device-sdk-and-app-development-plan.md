# Swift 端 Device SDK 与 Device App 开发计划

本文面向本期 Swift 端开发工作，目标是先完成一套可运行、可测试、可迁移到其他语言的 Swift Device SDK 和 Swift Device App。其他语言 SDK 和 App 不在本期实现范围内，待 Swift 端链路稳定后再按同一契约扩展。

## 1. 本期目标

本期开发分成两大部分：

1. **Swift Device SDK 开发**：在 `devices/swift/` 中实现端侧标准 SDK，封装注册、心跳、control/stream WebSocket、标准事件状态机、默认硬件 adapter、speaker 播放 buffer、水位线流控和 `custom.*` 语法糖。
2. **Swift Device App 开发**：在 `examples/for-blind-app/devices/native-ios-phone/` 中把参考 App 改造成 SDK 使用示例，App 只通过标准接入代码启用硬件、注册业务回调和启动 SDK。

最终 Swift App 的主代码应接近：

```swift
let client = DeviceClient(
    serverURL: "ws://127.0.0.1:8765",
    deviceID: "phone-001",
    userID: "user-001",
    name: "Phone device",
    audioInput: .enabled(),
    camera: .enabled(),
    speaker: .enabled(buffer: .default)
)

client.onCustomCommand("haptic.vibrate") { ctx in
    let durationMs = ctx.payload["duration_ms"] as? Int ?? 120
    try await haptics.vibrate(durationMs: durationMs)
    try await ctx.emit("custom.haptic.vibrate.done", ["duration_ms": durationMs])
}

client.onEvent("custom.navigation.route.updated") { event in
    try await navigation.update(event.payload)
}

try await client.start()
```

## 2. 非目标

本期不做：

- 不实现 Python、TypeScript、Kotlin、C 等其他语言的新 SDK 语法糖。
- 不把 for-blind-app 的业务逻辑写进 Swift SDK。
- 不要求 App 开发者手写 `control.device.register.requested`、心跳、音频 session、stream 生命周期事件或媒体 chunk。
- 不把非 speaker 输出继续复用标准 `stream.output.*`。
- 不强制 `custom.command.*` 返回 `completed/failed` 这类标准生命周期事件。
- 不实现 WebRTC；本期仍使用 control WebSocket 和 stream WebSocket。

## 3. 责任边界

### 3.1 SDK 开发者负责

Swift Device SDK 开发者负责：

- 根据 App 配置生成设备 profile、`supports` 和 `properties`。
- 建立 `/ws/control`，发送注册事件，处理注册结果。
- 注册成功后自动心跳。
- 建立或复用 `/ws/stream?device_id=...`。
- 维护实时音频 session 状态机。
- 显式 enable 后接入默认麦克风、相机和喇叭 adapter。
- 允许 App 覆盖默认 adapter。
- 封装 `sensor.mic` 上行 PCM chunk。
- 封装 `sensor.rgb` 连续帧采样和 chunk。
- 封装 `actuator.speaker` 下行播放 buffer、播放 drain、cancel、close。
- 根据 speaker buffer 水位线发送 `downstream.pause.requested` / `downstream.resume.requested`。
- 路由 `custom.*`，提供 `onCustomCommand(...)`、`onEvent(...)` 和 `ctx.emit(...)`。
- 提供诊断快照、错误状态和契约测试。

### 3.2 App 开发者负责

Swift Device App 开发者负责：

- 配置 `serverURL`、`deviceID`、`userID` 和展示名称。
- 显式启用需要的硬件能力：`audioInput`、`camera`、`speaker`。
- 在需要时覆盖默认硬件 adapter。
- 注册自定义业务回调，例如 `onCustomCommand(...)` 或 `onEvent(...)`。
- 处理 iOS 权限、UI 状态、App 生命周期和业务界面。
- 在业务 handler 中通过 `ctx.emit(...)` 回报自定义业务结果。

App 开发者不应该直接处理标准协议事件。

## 4. Swift Device SDK 开发计划

### 阶段 1：配置与注册

目标：让 App 只通过 Swift 配置创建设备并完成注册。

开发内容：

- 新增或调整 `DeviceClient` / `RealtimeAgentDeviceClient` 初始化配置。
- 支持 `AudioInput.disabled/enabled`、`Camera.disabled/enabled`、`Speaker.disabled/enabled`。
- 根据 enable 配置生成注册 payload：
  - `audioInput.enabled` -> `properties.realtime_agent.audio_input=sensor.mic`
  - `speaker.enabled` -> `properties.realtime_agent.audio_output=actuator.speaker`
  - `camera.enabled` -> `supports.sensors[].type=rgb`
- 注册成功后自动心跳。
- 注册失败时向 App 暴露错误。

验收：

- 单元测试覆盖 registration payload 生成。
- loopback 测试验证 `control.device.register.requested`、`registered`、heartbeat。
- App 不需要手写注册 JSON。

### 阶段 2：control / stream 通道

目标：SDK 内部封装 WebSocket 通讯和 stream chunk 编解码。

开发内容：

- control WebSocket client。
- stream WebSocket client。
- `RealtimeAgentEvent` encode/decode。
- `RealtimeAgentStreamChunk` encode/decode。
- control event 统一 dispatch。
- stream chunk 按 `stream_id` 分发。
- 基础重连和关闭流程。

验收：

- Swift Package 测试覆盖 event、chunk codec。
- loopback server 测试覆盖 control 和 stream 双通道。

### 阶段 3：实时音频输入

目标：显式启用 `audioInput` 后，SDK 自动维护 `sensor.mic` 上行。

开发内容：

- iOS 默认麦克风 adapter。
- PCM16LE / 16000Hz / mono / 20ms 输出能力。
- `control.audio_session.open.requested` -> 打开或确认 mic source 可读。
- 发送 `control.audio_session.opened`。
- 持续发送 `StreamChunk sensor.mic`。
- session close 时停止上行。
- 支持 App 覆盖 mic source。

验收：

- 模拟 mic source 的单元测试。
- loopback 测试验证 open 后持续产生 `sensor.mic` chunk。
- App 无需手写 chunk。

### 阶段 4：实时视频输入

目标：显式启用 `camera` 后，SDK 自动响应 `sensor.rgb` 连续视频请求。

开发内容：

- iOS 默认相机 adapter。
- JPEG frame 编码。
- 处理 `stream.control.open.requested (sensor.rgb, mode=continuous)`。
- 发送 `stream.input.opened`。
- 按 `frequency_hz` 持续上传 RGB chunk。
- 处理 `stream.control.close.requested` 和会话关闭。
- 支持 App 覆盖 camera source。

验收：

- 使用 mock camera source 的测试。
- iOS Simulator 或真机验证相机权限和帧上传。

### 阶段 5：speaker 下行播放与水位线

目标：显式启用 `speaker` 后，SDK 自动播放 server 下发音频并做流控。

开发内容：

- iOS 默认 speaker adapter。
- SDK 内置 playback buffer。
- 默认配置：
  - `start_watermark_ms=120`
  - `low_watermark_ms=300`
  - `high_watermark_ms=800`
  - `max_buffer_ms=1200`
- 处理 `stream.output.open.requested (actuator.speaker)`。
- 接收 `StreamChunk actuator.speaker` 并写入 SDK buffer。
- 到启动水位线后写入 speaker sink，并发送 `stream.output.started`。
- 到高水位线后发送 `downstream.pause.requested`。
- 到低水位线后发送 `downstream.resume.requested`。
- close 时等待 SDK buffer 和 sink drain 后发送 `stream.output.closed`。
- cancel 时立即清空 buffer、停止 sink，并发送 `stream.output.closed` 或 `stream.output.cancelled`。

验收：

- buffer 水位线单元测试。
- pause/resume 事件测试。
- cancel 清空 buffer 测试。
- iOS 播放基本可听验证。

### 阶段 6：custom 事件语法糖

目标：App 不直接处理标准事件，只处理业务自定义事件。

开发内容：

- `onCustomCommand(commandName, handler)`。
- `onEvent(eventName, handler)`，只允许 `custom.*`。
- `CustomCommandContext.payload`。
- `CustomCommandContext.emit(eventName, payload)`，只允许发送 `custom.*`。
- `custom.command.requested` 路由到对应 command handler。
- 其他 `custom.<domain>.*` 路由到 `onEvent`。
- 标准事件不得投递给 `onEvent`。

验收：

- `custom.command.requested` 调用 handler。
- handler 可通过 `ctx.emit(...)` 发送业务结果。
- 标准 `stream.output.*` 不触发 `onEvent`。

## 5. Swift Device App 开发计划

### 阶段 1：接入新版 Swift SDK

目标：参考 App 只使用 Device SDK 标准入口，不再保留重复协议运行时。

开发内容：

- 清理 App 中重复的本地 event / stream codec。
- 使用 `DeviceClient` 初始化。
- 读取 `AppConfig.json` 后映射到 SDK config。
- UI 上展示连接、注册、音频、相机、speaker 状态。

验收：

- Simulator 能连接 server 并注册。
- UI 能显示注册状态和错误。

### 阶段 2：显式启用硬件

目标：App 通过配置启用硬件，而不是手写协议。

开发内容：

- 支持配置项：
  - `audio_input.enabled`
  - `camera.enabled`
  - `speaker.enabled`
  - `speaker.buffer.*`
- 默认不开启任何音视频能力。
- 开启后由 SDK 注册能力并接入默认 adapter。

验收：

- 禁用全部硬件时，设备只作为自定义事件节点注册。
- 启用 camera 后，注册 payload 包含 `sensor.rgb`。
- 启用 audio/speaker 后，注册 payload 包含系统音频 properties。

### 阶段 3：业务自定义事件

目标：参考 App 展示推荐的业务扩展方式。

开发内容：

- 注册 `onCustomCommand("haptic.vibrate", ...)`。
- 注册一个 `onEvent("custom.navigation.route.updated", ...)` 示例。
- handler 内使用 `ctx.emit(...)` 回报业务结果。

验收：

- loopback server 下发 `custom.command.requested` 能触发 App handler。
- App 能回发 `custom.haptic.vibrate.done`。

### 阶段 4：端到端联调

目标：Swift App 能作为真实 Device 接入主示例 server。

联调流程：

1. 启动 server：

   ```bash
   uv run realtime-agent.server.run --config examples/for-blind-app/agent-server/server.yaml
   ```

2. 启动 iOS App。
3. App 连接并注册。
4. 触发实时对话。
5. 观察：
   - `control.device.registered`
   - `control.audio_session.opened`
   - `StreamChunk sensor.mic`
   - `stream.input.opened/closed (sensor.rgb)`
   - `stream.output.started/closed (actuator.speaker)`
   - `downstream.pause.requested/resume.requested`
   - `custom.*` 业务事件

验收：

- server `/api/debug/devices` 能看到 Swift device。
- runs 目录有对应 `events.jsonl`、`stream-events.jsonl`、`audio/`。
- App 能在 cancel 时停止 speaker 并清空 buffer。

## 6. 测试分层

| 层级 | 目标 | 建议位置 |
| --- | --- | --- |
| Swift 单元测试 | event、chunk、profile、buffer、callback registry | `devices/swift/Tests/` |
| Swift loopback 测试 | control/stream 双通道、注册、心跳、custom 事件 | `devices/swift/Tests/` |
| 协议契约测试 | 确认 Swift SDK 行为符合标准协议 | `devices/swift/Tests/` 或 `agent-server/protocol-tests/` |
| iOS 构建测试 | 确认参考 App 能构建 | `examples/for-blind-app/devices/native-ios-phone/` |
| 真机联调 | 确认真实麦克风、相机、喇叭 | 手工流程记录到 App README |

## 7. 交付顺序

建议按以下顺序提交：

1. Swift SDK config/profile 和注册 payload 生成。
2. Swift SDK control/stream WebSocket。
3. Swift SDK custom 事件语法糖。
4. Swift SDK audio input 默认 adapter。
5. Swift SDK camera 默认 adapter。
6. Swift SDK speaker adapter、playback buffer 和 pause/resume。
7. iOS App 接入新版 SDK。
8. iOS App 端到端联调与 README 更新。

每一步都应有可运行测试或明确的手工验证方式，不把“协议资产检查通过”写成“真机已验证”。

## 8. 后续多语言扩展

Swift 端稳定后，再按同一契约扩展其他语言：

- Python Device SDK。
- TypeScript Device SDK。
- Kotlin / Android Device SDK。
- C / 嵌入式 Device SDK。

扩展时不重新设计协议，只复用本期沉淀的 App 接入 API、SDK 状态机、`custom.*` 路由和 speaker buffer 行为。
