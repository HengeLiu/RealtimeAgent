# RealtimeAgentDeviceKit

`RealtimeAgentDeviceKit` 是 Swift Package 形式的 realtime-agent 端侧 Device SDK。它面向
iOS 和 macOS，负责协议数据模型、stream chunk 编解码、control / stream WebSocket、
设备注册、心跳、标准事件状态机、显式启用的音视频硬件接入、speaker 播放 buffer 水位线
以及 `custom.*` 业务事件语法糖。

本期 Swift 实现以 [Swift 端 Device SDK 与 Device App 开发计划](../../docs/reference/swift-device-sdk-and-app-development-plan.md)
为准。旧的 `IOS_DEVICE_SDK_DESIGN_PLAN.md` 只作为历史阶段记录，不再作为当前公开 API 边界。

## 遵循的协议

协议版本：`realtime-agent.v1`

| 通道 | 路径 | 用途 |
| --- | --- | --- |
| Control WebSocket | `/ws/control` | 注册、心跳、命令、stream 生命周期事件。 |
| Audio Input WebSocket | `/ws/stream/audio/input?device_id=<device_id>` | 端侧上传 `sensor.mic` PCM chunk。 |
| Audio Output WebSocket | `/ws/stream/audio/output?device_id=<device_id>` | 端侧接收 server 下发的 `actuator.speaker` chunk。 |
| Visual Input WebSocket | `/ws/stream/visual/input?device_id=<device_id>` | server 请求后，端侧上传一帧 `sensor.rgb` 图片。 |

控制事件信封：

```swift
RealtimeAgentEvent(
    eventName: "control.device.register.requested",
    userID: "user-001",
    producerID: "dev-ios-001",
    payload: [:]
)
```

stream chunk 二进制格式：

```text
4 bytes big-endian header length
JSON header bytes
payload bytes
```

`RealtimeAgentStreamChunkCodec` 会校验 `payload_size`。

## 标准接入示例

App 推荐只使用 `DeviceClient` 标准入口。麦克风、相机和喇叭默认禁用，必须显式 enable 后
SDK 才会注册能力并维护对应链路：

```swift
import RealtimeAgentDeviceKit

let client = try DeviceClient(
    serverURL: "http://127.0.0.1:8765",
    deviceID: "dev-ios-phone-001",
    userID: "user-001",
    name: "iOS Phone",
    audioInput: .enabled(),
    camera: .enabled(source: cameraFrameSource),
    speaker: .enabled(buffer: .default)
)

client.onCustomCommand("haptic.vibrate") { context in
    let durationMS = context.payload["duration_ms"] as? Int ?? 120
    try await haptics.vibrate(durationMS: durationMS)
    try await context.emit("custom.haptic.vibrate.done", ["duration_ms": durationMS])
}

client.onEvent("custom.navigation.route.updated") { event in
    try await navigation.update(event.payload)
}

try await client.connectAndRegister()
```

`onEvent(...)` 只接受 `custom.*` 事件；标准 `control.*`、`stream.*`、`downstream.*`
由 SDK 内部状态机消费，不会再投递给 App 的自定义事件 handler。

## 数据模型

### `RealtimeAgentDevice`

用于构建设备注册 payload：

```swift
let device = RealtimeAgentDevice(deviceID: "dev-ios-001")
    .user("user-001")
    .named("iPhone")
    .role("phone")
    .sensorRgb(modes: ["single"], format: "jpeg")
    .actuatorVibrator(commands: ["vibrate"])

let payload = device.registrationPayload
```

### `RealtimeAgentEvent`

控制事件信封，字段与 server `Event.to_dict()` 对齐：

- `version`
- `event_id`
- `event_name`
- `timestamp_ms`
- `user_id`
- `producer_id`
- `session_id`
- `stream_id`
- `stream_type`
- `payload`

### `RealtimeAgentStreamChunk`

表达媒体 WebSocket 的 header 和 payload：

```swift
let chunk = RealtimeAgentStreamChunk(
    userID: "user-001",
    sessionID: "dev-ios-001",
    streamID: "stream-rgb-001",
    streamType: "sensor.rgb",
    seq: 0,
    payload: jpegData,
    codec: "jpeg",
    sampleRate: 1,
    channels: 1,
    durationMS: 0,
    final: true,
    metadata: ["request_id": "req-001"]
)

let data = try RealtimeAgentStreamChunkCodec.encode(chunk)
```

## 音频、RGB 和 speaker 适配器

`AudioInput.enabled(source:)` 可以注入麦克风数据源。数据源只需要产出已经转换好的
PCM16LE 16 kHz mono chunk；SDK 在收到 `control.audio_session.open.requested` 后自动回
`control.audio_session.opened` 并持续发送 `sensor.mic` chunk：

```swift
struct AppMicrophoneSource: RealtimeAgentMicrophoneSource {
    func streamPCM16LE(configuration: RealtimeAgentMicrophoneConfiguration) -> AsyncThrowingStream<Data, Error> {
        audioEngine.streamPCM16LE(chunkMS: configuration.chunkMS)
    }
}

let client = try DeviceClient(..., audioInput: .enabled(source: AppMicrophoneSource()))
```

当前 Swift SDK 已提供基于 AVFoundation 的默认麦克风、相机和 speaker adapter。
App 仍可以传入 source/sink 覆盖默认 adapter，例如使用外接麦克风、测试音频文件、
图片样例或自定义播放器。

`Camera.enabled(source:)` 可以注入 JPEG frame source。SDK 会响应
`stream.control.open.requested(sensor.rgb, mode=single, sample_count=1)`，在 server 请求后
上传一帧 JPEG，然后关闭该逻辑输入流：

```swift
let camera = ClosureCameraFrameSource {
    try await captureJPEGFromAppCamera()
}

let client = try DeviceClient(..., camera: .enabled(source: camera))
```

`Speaker.enabled(buffer:sink:)` 启用 SDK 内置 speaker 播放 buffer。App 只配置水位线或注入
真实播放器 sink；SDK 会按 buffer 状态发送 `stream.output.started`、
`downstream.pause.requested`、`downstream.resume.requested`、`stream.output.closed` 和
`stream.output.cancelled`：

```swift
let speaker = Speaker.enabled(
    buffer: PlaybackBuffer(startWatermarkMS: 600, lowWatermarkMS: 3000, highWatermarkMS: 12000, maxBufferMS: 20000),
    sink: AppSpeakerSink()
)
```

## 导入到自己的项目

### 通过 Swift Package Manager 引入本地包

1. 在 Xcode 中打开你的 iOS/macOS 工程。
2. 选择 `File > Add Package Dependencies...`。
3. 如果在同一个工作区，可以选择本地路径：

   ```text
   /path/to/OpenAIglassesDemo/devices/swift
   ```

4. 把 `RealtimeAgentDeviceKit` 加到目标 target。
5. 代码中导入：

   ```swift
   import RealtimeAgentDeviceKit
   ```

### 复制到独立仓库

也可以把 `devices/swift` 复制为独立 Swift Package，通过 Git URL
引入。后续发布时建议给仓库打 tag，例如 `0.1.0`。

## 最小注册 payload 示例

```swift
import RealtimeAgentDeviceKit

let device = RealtimeAgentDevice(deviceID: "dev-ios-phone-001")
    .user("user-001")
    .named("iOS Phone")
    .role("phone")
    .sensorRgb(modes: ["single"], format: "jpeg")

let event = RealtimeAgentEvent(
    eventName: "control.device.register.requested",
    userID: "user-001",
    producerID: "dev-ios-phone-001",
    payload: device.registrationPayload
)

let json = try JSONSerialization.data(withJSONObject: event.dictionary)
```

## 和现有 iOS 参考端的关系

当前 iOS 参考端已经使用 `DeviceClient` 标准入口。App target 只保留配置读取、UI、
直连相机接收服务和业务自定义回调；注册、心跳、标准事件路由、speaker buffer 和
`custom.*` 路由由 SDK 接管。

## 测试

```bash
cd devices/swift
swift test
```

测试覆盖：

- stream chunk 黄金样例读取。
- stream chunk 对象往返。
- 设备注册 payload 构造。
- 控制事件 JSON 往返和异常 payload 校验。
- 设备注册、显式硬件配置生成 profile、RGB stream helper、output 生命周期。
- `custom.command.requested`、`custom.*` 事件路由，以及标准事件不进入 `onEvent`。
- 音频会话打开后发送 `control.audio_session.opened` 并消费注入的 mic source。
- speaker buffer 的 started、pause、resume、cancel 和 close 行为。

## 当前限制

- SDK 已封装 `URLSessionWebSocketTask` 通讯客户端和标准事件状态机，但真机后台切换、
  网络断线重连和长连接抖动还未验证。
- iOS 默认 adapter 已接入 AVFoundation；真机权限弹窗、后台切换、蓝牙路由和复杂音频会话
  仍需真实设备验证。
- 尚未发布为远程 Swift Package tag。
