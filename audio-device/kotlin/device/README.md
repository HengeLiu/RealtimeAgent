# audio-chat-device Kotlin SDK

`audio-chat-device` 是 Kotlin 端侧通讯 SDK，完整实现了 `audio-chat.v1` 协议。

## 功能特性

- **协议实现**：完整的事件信封（AudioChatEvent）和二进制流（StreamChunk）编解码
- **WebSocket 连接**：自动重连、心跳维持、双通道（控制 + 流）
- **命令回调**：自动分发给注册的命令处理器
- **设备声明**：通过 Builder 模式声明设备能力

## 模块结构

| 文件 | 说明 |
|------|------|
| `AudioChatDevice.kt` | 设备声明构建器 |
| `AudioChatEvent.kt` | 事件信封 + DeviceSupports 数据类 |
| `StreamChunk.kt` | Stream 数据块 + 编解码器 |
| `AudioChatClient.kt` | WebSocket 客户端（连接、事件、命令回调） |
| `WebSocketConnection.kt` | WebSocket 连接接口 + OkHttp 实现 |
| `Constants.kt` | 常量定义（EventTypes, StreamTypes, DeviceConstants） |
| `GsonFactory.kt` | JSON 序列化工厂 |

## 快速开始

```kotlin
// 1. 构建设备
val device = AudioChatDevice.define("device-001")
    .user("user-001")
    .asPhone()

// 2. 创建客户端
val client = AudioChatClient(
    serverUrl = "http://127.0.0.1:8765",
    device = device
)

// 3. 设置监听器
client.listener = object : AudioChatClientListener {
    override fun onConnected() {
        println("已连接")
    }

    override fun onCommandRequested(commandId: String, taskType: String, payload: Map<String, Any?>) {
        println("收到命令: $taskType")
        // 处理命令后发送完成事件
        client.sendCommandCompleted(commandId)
    }

    override fun onStreamChunk(chunk: StreamChunk) {
        println("收到流数据: ${chunk.stream_type}")
    }
}

// 4. 连接服务器
client.connect()

// 5. 连接流通道（如需要上传音视频）
client.connectStream()
```

## 设备类型

```kotlin
// 手机设备（麦克风 + 扬声器）
val phone = AudioChatDevice.define("phone-001").user("user-001").asPhone()

// 眼镜设备（摄像头）
val glass = AudioChatDevice.define("glass-001").user("user-001").asGlass()

// 自定义设备
val custom = AudioChatDevice.define("custom-001")
    .user("user-001")
    .role("glass")
    .sensorRgb(modes = listOf("single", "continuous"))
    .sensorMic()
    .actuatorVibrator()
```

## 事件类型

```kotlin
// 发送命令完成
client.sendCommandCompleted(commandId, taskId = "task-001", taskType = "haptic.vibrate")

// 发送流打开
client.sendStreamInputOpened(streamId, streamType = StreamTypes.SENSOR_RGB)

// 上传图片
val jpegData = File("frame.jpg").readBytes()
val chunk = StreamChunkCodec.createImageChunk(
    userId = "user-001",
    sessionId = "session-001",
    streamId = "stream-001",
    jpegData = jpegData,
    requestId = "req-001"
)
client.sendChunk(chunk)
```

## 添加依赖

```kotlin
// build.gradle.kts
dependencies {
    implementation("io.audiochat:device:0.1.0")
}
```

或使用 Maven：

```xml
<dependency>
    <groupId>io.audiochat</groupId>
    <artifactId>device</artifactId>
    <version>0.1.0</version>
</dependency>
```