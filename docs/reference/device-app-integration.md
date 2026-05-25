# 端侧 App 接入指南

本文面向端侧 App 开发者，目标是说明“用最少的标准代码接入 Device SDK”。App 开发者不需要理解 control WebSocket、stream WebSocket、标准事件状态机或媒体 chunk 封装；这些由各语言 Device SDK 负责。

## 1. App 开发者负责什么

App 开发者只负责四类事情：

1. 创建设备 client，填写设备身份、server 地址和需要启用的硬件能力。
2. 显式启用麦克风、相机、喇叭等内置硬件能力；默认都不启用。
3. 注册业务自定义回调，例如 `on_custom_command(...)` 或 `on_event("custom.*", ...)`。
4. 启动 SDK，并在 App 生命周期结束时关闭 SDK。

App 开发者不负责：

- 手写 `/ws/control` 或 `/ws/stream` 连接。
- 手写 `control.device.register.requested`、心跳、音频 session、stream 生命周期事件。
- 手写麦克风 PCM chunk、相机帧 chunk 或 speaker 下行 chunk。
- 实现 speaker 播放 buffer、水位线、`downstream.pause.requested` / `downstream.resume.requested`。
- 判断用户是否打断输出；端侧只响应 SDK 内部收到的 server cancel。

## 2. 默认硬件策略

SDK 默认不注册任何音视频硬件能力。App 必须显式启用，SDK 才会尝试使用平台默认硬件 adapter：

| 能力 | 默认 | 显式启用后 SDK 做什么 | App 何时需要自定义 adapter |
| --- | --- | --- | --- |
| 麦克风 `sensor.mic` | 禁用 | SDK 打开或接入平台默认麦克风，维护音频上行 chunk | 使用外部麦克风、音频文件、测试样例或平台默认 adapter 不可用 |
| 相机 `sensor.rgb` | 禁用 | SDK 打开或接入平台默认相机，按 server 请求频率上传帧 | 使用特定镜头、图片样例、视频文件或平台默认 adapter 不可用 |
| 喇叭 `actuator.speaker` | 禁用 | SDK 打开或接入平台默认播放器，维护下行播放 buffer 和水位线 | 使用自定义播放器、蓝牙设备、文件输出或平台默认 adapter 不可用 |

不需要音视频的设备可以只作为自定义事件消费节点运行，例如独立算力节点、网关、控制器。

## 3. 设备注册和能力声明

App 开发者不需要手写注册 JSON，也不需要直接维护 `supports`、`properties`、心跳或事件路由。Device SDK 会根据 client 配置自动生成注册事件：

| App 配置 | SDK 自动生成 |
| --- | --- |
| `audio_input=AudioInput.enabled()` | `properties.realtime_agent.audio_input=sensor.mic` |
| `speaker=Speaker.enabled(...)` | `properties.realtime_agent.audio_output=actuator.speaker` |
| `camera=Camera.enabled()` | `supports.sensors[].type=rgb` |
| `on_custom_command("haptic.vibrate", ...)` | 可消费 `custom.command.requested` 中的 `payload.command=haptic.vibrate` |
| `on_event("custom.navigation.route.updated", ...)` | 可消费对应 `custom.*` 事件 |

SDK 内部仍然会发送标准 `control.device.register.requested`，但 App 开发者只面对配置 API。

## 4. 标准接入代码

下面是目标 SDK 设计形态，不表示当前所有语言 SDK 已经实现。

### Python

```python
client = DeviceClient(
    server_url="ws://127.0.0.1:8765",
    device_id="phone-001",
    user_id="user-001",
    name="Phone device",
    audio_input=AudioInput.enabled(),      # 默认禁用，显式启用后 SDK 使用默认麦克风
    camera=Camera.enabled(),               # 默认禁用，显式启用后 SDK 使用默认相机
    speaker=Speaker.enabled(
        buffer=PlaybackBuffer.default()
    ),
)

async def vibrate(ctx):
    duration_ms = ctx.payload.get("duration_ms", 120)
    await device.haptics.vibrate(duration_ms)
    await ctx.emit("custom.haptic.vibrate.done", {"duration_ms": duration_ms})

async def update_route(event):
    await app.navigation.update(event.payload)

client.on_custom_command("haptic.vibrate", vibrate)
client.on_event("custom.navigation.route.updated", update_route)

await client.start()
```

### Swift

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

### Java

```java
DeviceClient client = DeviceClient.builder()
    .serverUrl("ws://127.0.0.1:8765")
    .deviceId("phone-001")
    .userId("user-001")
    .name("Phone device")
    .audioInput(AudioInput.enabled())
    .camera(Camera.enabled())
    .speaker(Speaker.enabled(PlaybackBuffer.defaults()))
    .build();

client.onCustomCommand("haptic.vibrate", ctx -> {
    int durationMs = ctx.payload().getInt("duration_ms", 120);
    haptics.vibrate(durationMs);
    ctx.emit("custom.haptic.vibrate.done", Map.of("duration_ms", durationMs));
});

client.onEvent("custom.navigation.route.updated", event -> {
    navigation.update(event.payload());
});

client.start();
```

### C

```c
ra_device_client_config_t config = {
    .server_url = "ws://127.0.0.1:8765",
    .device_id = "phone-001",
    .user_id = "user-001",
    .name = "Phone device",
    .audio_input = RA_AUDIO_INPUT_ENABLED_DEFAULT,
    .camera = RA_CAMERA_ENABLED_DEFAULT,
    .speaker = RA_SPEAKER_ENABLED_DEFAULT_BUFFER,
};

ra_device_client_t *client = ra_device_client_create(&config);

ra_client_on_custom_command(client, "haptic.vibrate", on_vibrate, NULL);
ra_client_on_event(client, "custom.navigation.route.updated", on_route_updated, NULL);

ra_device_client_start(client);
```

## 5. 自定义事件和语法糖

所有业务扩展事件都必须使用 `custom.*` 命名空间。App 开发者优先使用语法糖：

| API | 用途 |
| --- | --- |
| `on_custom_command(name, handler)` | 注册 `custom.command.requested` 中某个 `payload.command` 的业务处理函数。 |
| `on_event("custom.<domain>.<name>", handler)` | 注册普通自定义事件。 |
| `ctx.emit("custom.<domain>.<name>", payload)` | 在 handler 中发送自定义事件给 server。 |

`custom.command.*` 不强制标准回执。App 可以用 `ctx.emit(...)` 发任意自定义事件表达业务结果，例如 `custom.haptic.vibrate.done` 或 `custom.navigation.route.failed`。

App 开发者不应使用标准 `command.*` 或 `stream.output.*` 做业务扩展。标准 `stream.output.*` 只给 SDK 内置 speaker 播放链路使用。

App 不需要手写事件 routes。只要在 `connect/register` 前调用 `on_custom_command(...)` 或 `on_event(...)`，Device SDK 会在设备注册时自动声明对应的 `custom.*` 消费能力，server 会按声明路由下发事件。

## 6. 播放 buffer 配置

如果启用 speaker，SDK 默认使用以下播放 buffer 配置：

```text
start_watermark_ms = 120
low_watermark_ms = 300
high_watermark_ms = 800
max_buffer_ms = 1200
```

App 可以覆盖这些值：

```python
speaker = Speaker.enabled(
    buffer=PlaybackBuffer(
        start_watermark_ms=160,
        low_watermark_ms=320,
        high_watermark_ms=900,
        max_buffer_ms=1400,
    )
)
```

App 只配置 buffer 参数，不实现 buffer 队列，也不直接发送 `downstream.pause.requested` / `downstream.resume.requested`。

## 7. 只消费自定义事件的设备

设备也可以不启用任何音视频能力，只作为自定义事件消费节点：

```python
client = DeviceClient(
    server_url="ws://127.0.0.1:8765",
    device_id="compute-node-001",
    user_id="user-001",
    name="Compute node",
    audio_input=AudioInput.disabled(),
    camera=Camera.disabled(),
    speaker=Speaker.disabled(),
)

client.on_custom_command("local.infer", run_local_inference)
await client.start()
```

这类设备仍然会完成注册、心跳和自定义事件分发，但不会注册 `sensor.mic`、`sensor.rgb` 或 `actuator.speaker`。
