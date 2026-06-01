# 端侧 App 接入指南

本文面向端侧 App 开发者，目标是说明“用最少的标准代码接入 Device SDK”。App 开发者不需要理解 control WebSocket、多条媒体 WebSocket、标准事件状态机或媒体 chunk 封装；这些由各语言 Device SDK 负责。

当前推荐从 `examples/device_demo` 验证端侧接入。Swift Device SDK 已经具备真机 demo 入口；TypeScript / JavaScript SDK 主要覆盖浏览器和 Node 端侧；Python SDK 主要作为基准 SDK、开发支持组件和测试 harness；Kotlin / Java 与 C / ESP32 仍在补齐完整端侧能力的过程中。

## 1. App 开发者负责什么

App 开发者只负责五类事情：

1. 创建设备 client，填写设备身份、server 地址和需要启用的硬件能力。
2. 显式启用麦克风、相机、喇叭等内置硬件能力；默认都不启用。
3. 注册业务自定义回调，例如 `on_custom_command(...)` 或 `on_event("custom.*", ...)`。
4. 注册 SDK 连接状态回调，并决定断连后的 App 行为，例如手动重连、后台重试或只展示错误。
5. 启动 SDK，并在 App 生命周期结束时关闭 SDK。

App 开发者不负责：

- 手写 `/ws/control`、音频上行、音频下行或视觉上行 WebSocket 连接。
- 手写 `control.device.register.requested`、心跳、音频 session、stream 生命周期事件。
- 手写麦克风 PCM chunk、相机帧 chunk 或 speaker 下行 chunk。
- 实现 speaker 播放 buffer、水位线、`downstream.pause.requested` / `downstream.resume.requested`。
- 判断用户是否打断输出；端侧只响应 SDK 内部收到的 server cancel。
- 在断连时清理麦克风、相机、speaker、stream 或旧会话资源；这些必须由 SDK 的断连收口统一完成。

## 2. 默认硬件策略

SDK 默认不注册任何音视频硬件能力。App 必须显式启用，SDK 才会尝试使用平台默认硬件 adapter：

| 能力 | 默认 | 显式启用后 SDK 做什么 | App 何时需要自定义 adapter |
| --- | --- | --- | --- |
| 麦克风 `sensor.mic` | 禁用 | SDK 打开或接入平台默认麦克风，维护音频上行 chunk | 使用外部麦克风、音频文件、测试样例或平台默认 adapter 不可用 |
| 相机 `sensor.rgb` | 禁用 | SDK 打开或接入平台默认相机，收到 server 单帧采集请求后上传一张图片 | 使用特定镜头、图片样例、视频文件或平台默认 adapter 不可用 |
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
SDK 内部应把媒体传输拆成多条物理链路：`sensor.mic` 使用音频上行链路，
`actuator.speaker` 使用音频下行链路，`sensor.rgb` 或图片帧使用视觉上行链路。
视觉上行不是后台常驻采集；当前设计是一条请求触发一张图片，只有 server 下发
`stream.control.open.requested (sensor.rgb, mode=single, sample_count=1)` 后才采集。
App 不需要关心这些 WebSocket 的建立、重连和背压。

注册成功不代表连接永远有效。Device SDK 会持续维护 heartbeat 和控制通道健康状态；当 SDK 发现 heartbeat 发送失败、control WebSocket 断开或关键媒体链路不可恢复时，会先完成本地资源收口，再向 App 发布连接状态。App 不需要清理底层资源，但必须决定“断连后做什么”。

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
client.on_connection_state_change(handle_connection_state)

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

client.onConnectionStateChange { state in
    await app.handleDeviceConnectionState(state)
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

client.onConnectionStateChange(state -> {
    app.handleDeviceConnectionState(state);
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
ra_client_on_connection_state_change(client, on_connection_state_changed, NULL);

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

App 开发者不应使用标准 `command.*` 或 `stream.output.*` 做业务扩展。标准 `stream.output.*` 只给 SDK 内置 speaker 播放链路使用，其中 `stream.output.start.requested` 表示开始一轮逻辑 speaker 输出，`stream.output.finished` 表示端侧确认本轮已播放完成。

App 不需要手写事件 routes。只要在 `connect/register` 前调用 `on_custom_command(...)` 或 `on_event(...)`，Device SDK 会在设备注册时自动声明对应的 `custom.*` 消费能力，server 会按声明路由下发事件。

## 6. 连接状态和断连后的 App 行为

Device SDK 必须把连接状态作为独立状态暴露给 App。App 不应该通过解析日志或轮询 debug API 判断是否断连。

推荐状态：

| 状态 | 含义 | App 推荐行为 |
| --- | --- | --- |
| `idle` | SDK 已创建但还没有连接。 | 展示初始化或等待启动状态。 |
| `connecting` | 正在建立 control WebSocket。 | 禁用开始通话按钮。 |
| `registering` | 正在发送设备注册并等待 server 回执。 | 显示注册中。 |
| `registered` | 设备已注册，heartbeat 正常。 | 允许用户开始通话。 |
| `disconnected(reason)` | SDK 已确认断连，并已释放本地资源。 | 展示重连入口或按 App 策略后台重试。 |
| `closed` | App 主动关闭 SDK。 | 不自动重连，除非 App 重新启动 client。 |

断连后的资源释放由 SDK 负责，包括停止 heartbeat、停止录音、取消视觉采集、停止 speaker、清空待播放 buffer、关闭 stream 和清理旧会话状态。App 不应再手动调用麦克风、相机或 speaker 的底层清理方法，否则容易和 SDK 状态机重复执行。

App 负责选择断连后的产品行为：

1. 手动重连：显示 `连接断开\n重连`、`注册失败\n重试` 等按钮，用户点击后重新调用 SDK 的注册流程。
2. 后台自动重试：在 App 自己的策略中做退避重试，例如前台每 2s/5s/10s 重试，后台降低频率或停止。
3. 只展示错误：某些低功耗设备或一次性任务设备可以停在错误页，等待用户重新进入。

Swift demo 推荐使用手动重连：断连后回到开始页，停止相机预览，主按钮显示 `连接断开\n重连`，点击后重新检查权限并重新注册。正式产品可以在 `disconnected(reason)` 中区分原因，例如 Wi-Fi 切换、server 关闭、heartbeat 失败、stream 长时间不可恢复，再决定是否自动重试。

伪代码：

```text
client.on_connection_state_change { state in
  switch state:
    case registered:
      app.enableStartConversationButton()
    case disconnected(reason):
      app.stopShowingConversationScreen()
      app.showReconnectButton(title = "连接断开\n重连")
      app.recordDiagnostic(reason)
    case closed:
      app.showStoppedState()
    default:
      app.showProgress(state.label)
}

onReconnectButtonTapped:
  app.showProgress("重新连接中")
  sdk.requestPermissionsIfNeeded()
  sdk.register()
```

重新注册必须走 SDK 的完整注册流程。App 不应保存或复用旧 `connection_id`，也不应假设旧媒体 stream 仍可用。SDK 重新注册成功后，server 会返回新的 `connection_id`，App 只根据 SDK 新状态更新 UI。

## 7. 播放 buffer 配置

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

App 只配置 buffer 参数，不实现 buffer 队列，也不直接发送 `downstream.pause.requested` / `downstream.resume.requested`。下行背压只作用在音频下行链路，不能阻塞麦克风或视觉上行。

播放 buffer 只用于正常播放链路中的抗抖和水位线控制，不能改变打断语义。收到
`stream.output.cancel.requested` 时，SDK 必须把当前 output stream 标记为已取消，
立即丢弃尚未播放的 SDK playback buffer，停止本轮 drain loop，并调用 speaker sink 的
`cancel()` 清理播放器内部队列、ring buffer 或平台播放节点。正常的 `finish` 才需要等待
buffer 和本地播放器 drain，然后由 SDK 发送 `stream.output.finished`；`cancel` 不允许等待已缓存音频播放完成。

因此，较大的 buffer 设置虽然能降低网络抖动导致的断音，但会增加实现风险：如果 SDK
只清理自己的队列，却没有穿透清理已经写入播放器、ring buffer 或系统音频队列的音频，
用户插话后仍可能听到旧回复残留。各语言 SDK 的默认 speaker adapter 必须把 cancel
作为最高优先级路径处理，并在测试中覆盖“finish 等待中收到 cancel”“大 buffer 中收到
cancel”“cancel 后迟到 chunk 被忽略”等场景。

## 8. 只消费自定义事件的设备

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
