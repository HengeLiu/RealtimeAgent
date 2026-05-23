# RealtimeAgentDeviceKit

`RealtimeAgentDeviceKit` 是 Swift Package 形式的 realtime-agent 端侧通讯 SDK。它面向
iOS 和 macOS，负责协议数据模型、stream chunk 编解码、WebSocket 通讯、注册、心跳、
command 分发、stream 生命周期 helper，以及可复用的音频 / RGB 适配器入口。

下一阶段 iOS 端可运行 SDK 的目标设计和分阶段开发计划见
[iOS 设备侧 SDK 设计文档与开发计划](IOS_DEVICE_SDK_DESIGN_PLAN.md)。

## 遵循的协议

协议版本：`realtime-agent.v1`

| 通道 | 路径 | 用途 |
| --- | --- | --- |
| Control WebSocket | `/ws/control` | 注册、心跳、命令、stream 生命周期事件。 |
| Stream WebSocket | `/ws/stream?device_id=<device_id>` | 二进制 stream 数据。 |

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

## 数据模型

### `RealtimeAgentDevice`

用于构建设备注册 payload：

```swift
let device = RealtimeAgentDevice(deviceID: "dev-ios-001")
    .user("user-001")
    .named("iPhone")
    .role("phone")
    .sensorRgb(modes: ["single", "continuous"], format: "jpeg", frequencyHz: 1)
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

表达 `/ws/stream` 的 header 和 payload：

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

## 最小通讯示例

```swift
import Foundation
import RealtimeAgentDeviceKit

let device = RealtimeAgentDevice(deviceID: "dev-ios-phone-001")
    .user("user-001")
    .named("iOS Phone")
    .role("phone")
    .sensorRgb(modes: ["single"], format: "jpeg", frequencyHz: 1)

let client = RealtimeAgentDeviceClient(
    serverURL: URL(string: "http://127.0.0.1:8765")!,
    device: device
)

client.onCommand("phone.scan_object") { command in
    try await command.accepted(["state": "started"])
    try await command.completed(["result": ["status": "ok"]])
}

client.onStreamOpen("sensor.rgb") { request in
    let jpeg = Data([0xFF, 0xD8, 0xFF, 0xD9])
    try await request.opened(["request_id": request.requestID ?? ""])
    try await request.write(
        jpeg,
        codec: "jpeg",
        sampleRate: 1,
        channels: 1,
        final: true,
        metadata: ["request_id": request.requestID ?? ""]
    )
    try await request.closed(reason: "frame_uploaded")
}

try await client.connectAndRegister()
```

## 音频和 RGB 适配器

`MicrophoneStreamer` 封装 `sensor.mic` 的输入 stream 生命周期，调用方可以把已经转换好的
PCM16LE 16 kHz mono payload 写入 SDK：

```swift
let pcmBytes = AudioPCMConverter.pcm16LE(fromFloat32: floatSamples)
let microphone = MicrophoneStreamer(client: client)
try await microphone.open()
try await microphone.sendPCM16LE(pcmBytes, final: false)
try await microphone.close()
```

`SpeakerPlayer` 可以绑定 `actuator.speaker` 输出 stream，把服务端下发的 payload 暂存在
缓冲区：

```swift
let speaker = SpeakerPlayer()
speaker.bind(to: client)
```

如果 App 要直接把 payload 交给自己的播放器，也可以改用 `onOutputChunk`：

```swift
client.onOutputChunk("actuator.speaker") { chunk, session in
    try await playPCMInAppAudioEngine(chunk.payload)
    if chunk.final {
        try await session.finished(reason: "played")
        try await session.closed(reason: "played")
    }
}
```

`CameraFrameUploader` 可以把 `stream.control.open.requested(sensor.rgb)` 映射为单帧 JPEG
上传，也可以按请求中的 `sample_count` / `frequency_hz` 做连续采样：

```swift
let camera = ClosureCameraFrameSource {
    try await captureJPEGFromAppCamera()
}
CameraFrameUploader.registerSingleFrameHandler(client: client, source: camera)
CameraFrameUploader.registerFrameHandler(client: client, source: camera)
```

当前 SDK 负责协议生命周期和 chunk 收发；真实 `AVAudioEngine`、`AVAudioPlayerNode`、
`AVCaptureSession` 权限申请和硬件会话仍建议放在 App target 内，再把 PCM/JPEG bytes
交给这些适配器。

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
    .sensorRgb(modes: ["single"], format: "jpeg", frequencyHz: 1)

let event = RealtimeAgentEvent(
    eventName: "control.device.register.requested",
    userID: "user-001",
    producerID: "dev-ios-phone-001",
    payload: device.registrationPayload
)

let json = try JSONSerialization.data(withJSONObject: event.dictionary)
```

## 和现有 iOS 参考端的关系

当前仓库里的 iOS 参考端已有：

- `RealtimeAgentEvent.swift`
- `StreamChunkCodec.swift`
- `RealtimeAgentEndpointRuntime.swift`

`RealtimeAgentDeviceKit` 已经提供通用通讯客户端和协议 helper。当前 iOS 参考端已经引入
本地 Swift Package，并复用 SDK 的事件模型、stream codec、WebSocket 注册、心跳、事件分发
和 stream chunk 收发。后续迁移参考端时建议：

1. 把可复用的真实媒体采集和播放协议逻辑继续下沉到 SDK 适配器。
2. 保留相机、HTTP/WebSocket UI、配置读取和权限代码在 App target 中。

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
- 设备注册、command 分发、RGB stream helper、output 生命周期。
- `MicrophoneStreamer` 和 `CameraFrameUploader` 的协议级行为。

## 当前限制

- SDK 已封装 `URLSessionWebSocketTask` 通讯客户端，iOS 参考端已迁移到
  `RealtimeAgentDeviceClient`，但真机后台切换、网络断线重连和长连接抖动还未验证。
- iOS 真机权限、真实相机采集、麦克风采集和扬声器播放仍由 App target 自己实现。
- 尚未发布为远程 Swift Package tag。
