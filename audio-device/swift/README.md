# RealtimeAgentDeviceKit

`RealtimeAgentDeviceKit` 是 Swift Package 形式的 realtime-agent 端侧通讯 SDK。它面向
iOS 和 macOS，负责协议数据模型和 stream chunk 编解码。它不包含相机、麦克风、
扬声器、蓝牙或 UI 实现。

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

## 导入到自己的项目

### 通过 Swift Package Manager 引入本地包

1. 在 Xcode 中打开你的 iOS/macOS 工程。
2. 选择 `File > Add Package Dependencies...`。
3. 如果在同一个工作区，可以选择本地路径：

   ```text
   /path/to/OpenAIglassesDemo/audio-device/swift
   ```

4. 把 `RealtimeAgentDeviceKit` 加到目标 target。
5. 代码中导入：

   ```swift
   import RealtimeAgentDeviceKit
   ```

### 复制到独立仓库

也可以把 `audio-device/swift` 复制为独立 Swift Package，通过 Git URL
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

`RealtimeAgentDeviceKit` 是这些协议层的包化版本。下一步接入时建议：

1. 在 Xcode 工程中加入本地 Swift Package。
2. 用 `RealtimeAgentDeviceKit.RealtimeAgentEvent` 替换参考端本地事件信封。
3. 用 `RealtimeAgentDeviceKit.RealtimeAgentStreamChunkCodec` 替换本地 stream codec。
4. 保留相机、HTTP/WebSocket、UI 和权限代码在 App target 中。

## 测试

```bash
cd audio-device/swift
swift test
```

测试覆盖：

- stream chunk 黄金样例读取。
- stream chunk 对象往返。
- 设备注册 payload 构造。

## 当前限制

- SDK 只提供协议模型和 codec，没有封装 `URLSessionWebSocketTask` 客户端。
- iOS 真机权限、相机、麦克风和播放由 App 自己实现。
- 尚未发布为远程 Swift Package tag。
