# realtime-agent Kotlin / Java SDK

`realtime-agent Kotlin / Java SDK` 是 Android/JVM 端侧通讯 SDK。它采用 Kotlin-first
API，同时保留 Java 可调用的静态入口。当前版本提供协议数据模型、设备注册
payload 构造和 stream chunk 基础编解码。

## 遵循的协议

协议版本：`realtime-agent.v1`

| 通道 | 路径 | 用途 |
| --- | --- | --- |
| Control WebSocket | `/ws/control` | 注册、心跳、命令、stream 生命周期事件。 |
| Stream WebSocket | `/ws/stream?device_id=<device_id>` | 二进制 stream 数据。 |

控制事件字段和其他 SDK 保持一致：

```kotlin
RealtimeAgentEvent(
    eventName = "command.completed",
    userId = "user-001",
    producerId = "dev-android-001",
    payload = mapOf("command_id" to "cmd-001"),
)
```

stream 二进制帧：

```text
4 bytes big-endian header length
JSON header bytes
payload bytes
```

## 数据模型

### `RealtimeAgentDevice`

构建设备注册 payload：

```kotlin
val payload = RealtimeAgentDevice.define("dev-android-001")
    .user("user-001")
    .name("Android phone")
    .role("phone")
    .sensorRgb(modes = listOf("single", "continuous"), format = "jpeg", frequencyHz = 1)
    .actuatorVibrator()
    .registrationPayload()
```

生成字段：

- `device_id`
- `name`
- `device_name`
- `client_type`
- `sdk_version`
- `runtime`
- `properties`
- `supports`

### `RealtimeAgentEvent`

事件信封模型：

```kotlin
val event = RealtimeAgentEvent(
    eventName = "control.device.register.requested",
    userId = "user-001",
    producerId = "dev-android-001",
    payload = payload,
)
val data = event.toMap()
```

### `StreamChunk` / `StreamChunkCodec`

当前 Kotlin SDK 不强绑定 JSON 库，`StreamChunkCodec` 接收 header JSON 字符串：

```kotlin
val raw = StreamChunkCodec.encodeHeader(
    """{"stream_id":"stream-001","payload_size":3}""",
    "abc".toByteArray(),
)
val (headerJson, payload) = StreamChunkCodec.decodeHeader(raw)
```

项目接入 Android 后，可以用 Kotlin serialization、Moshi 或 Jackson 生成
header JSON，再交给 codec 编码。

## 导入到自己的项目

### 作为本地 Gradle module

1. 复制目录：

   ```text
   audio-device/kotlin/device
   ```

   到你的 Android/JVM 项目，例如：

   ```text
   your-project/realtime-agent-device/
   ```

2. 在根 `settings.gradle.kts` 中加入：

   ```kotlin
   include(":realtime-agent-device")
   project(":realtime-agent-device").projectDir = file("realtime-agent-device")
   ```

3. 在 App 或 JVM module 中依赖：

   ```kotlin
   dependencies {
       implementation(project(":realtime-agent-device"))
   }
   ```

4. 导入：

   ```kotlin
   import io.realtimeagent.device.RealtimeAgentDevice
   import io.realtimeagent.device.RealtimeAgentEvent
   import io.realtimeagent.device.StreamChunkCodec
   ```

### 发布到 Maven 后的导入方式

后续如果发布到 Maven Central，建议坐标：

```kotlin
dependencies {
    implementation("io.realtimeagent:device-client:0.1.0")
}
```

## Android WebSocket 建议

SDK 当前不绑定 WebSocket 实现。Android 项目建议使用 OkHttp：

```kotlin
val request = Request.Builder()
    .url("ws://127.0.0.1:8765/ws/control")
    .build()

val webSocket = okHttpClient.newWebSocket(request, listener)
```

收到 server 事件后解析为 Map，再按 `event_name` 分发；发送时使用
`RealtimeAgentEvent.toMap()` 生成字段。

## 测试

仓库没有提交 Gradle wrapper。本机有 Java 和 Gradle 时执行：

```bash
cd audio-device/kotlin
gradle test
```

如果项目补了 wrapper：

```bash
./gradlew test
```

当前测试草案覆盖：

- 设备注册 payload 构造。
- stream header 和 payload 编解码。

## 当前限制

- 本机开发环境尚未执行 Kotlin 测试，因为缺少 Java Runtime 和 Gradle。
- SDK 未绑定 JSON 库和 WebSocket 库，避免强行影响 Android 项目的技术选型。
- 尚未发布到 Maven Central。
