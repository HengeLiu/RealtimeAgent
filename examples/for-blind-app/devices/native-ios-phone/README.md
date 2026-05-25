# iOS phone reference endpoint

本目录提供一个最小可运行的 iOS phone 参考端。它用于验证 Swift Device SDK 的标准接入：
设备注册、显式启用音频 / 相机 / speaker、`/ws/stream` 二进制 stream、`sensor.rgb` 上传、
测试 `sensor.mic` 上传、`actuator.speaker` 输出消费和 `custom.*` 业务事件。
同时，它内置一个本地相机 WebSocket 接收服务，用于接收 ESP32 端直连推送的
JPEG 帧，并在 server 请求 `sensor.rgb` 时优先上传最近一帧。

它不是生产 App，也不负责唤醒词、AEC 或完整业务界面。App 只通过 `DeviceClient`
标准入口启用 SDK 能力、注册自定义业务回调和展示状态；标准协议事件由 SDK 内部消费。

## 目录结构

```text
RealtimeAgentPhone.xcodeproj
RealtimeAgentPhone/
  RealtimeAgentPhoneApp.swift
  ContentView.swift
  Core/
    AppConfig.swift
    RealtimeAgentEvent.swift      # 历史本地模型，当前不参与 target 编译
    StreamChunkCodec.swift        # 历史本地 codec，当前不参与 target 编译
    RealtimeAgentEndpointRuntime.swift
    IPAddressProvider.swift
    DirectCameraFrameCodec.swift
    DirectWebSocketFrameParser.swift
    DirectCameraSinkServer.swift
  Resources/
    AppConfig.json
    AppConfig.example.json
AppConfig.example.json
```

当前 Xcode 工程已经通过本地 Swift Package 依赖接入：

```text
../../../../devices/swift
```

参考端运行时使用 `RealtimeAgentDeviceKit` 提供的 `DeviceClient`、
`RealtimeAgentEvent`、`RealtimeAgentStreamChunk` 和 `RealtimeAgentStreamChunkCodec`。
本目录中历史同名文件只保留作迁移对照，不再参与 `RealtimeAgentPhone` target 编译。
App target 仍保留 UI、配置读取、直连相机接收服务和状态展示；通用注册、心跳、标准事件
分发、speaker buffer、水位线流控和 stream chunk 收发已经由 SDK 接管。

## 配置

App 启动时优先读取 bundle 内的 `AppConfig.json`，找不到时读取
`AppConfig.example.json`，再找不到才使用 Swift 代码中的本地默认配置。

推荐先从仓库根目录生成本地配置：

```bash
# 在项目根目录执行
uv run realtime-agent.config.sync \
  --output-dir examples/for-blind-app/agent-server/config/generated \
  --server-url http://127.0.0.1:8765 \
  --user-id user-endpoint-001
```

然后把生成的 iOS 配置覆盖到 App 资源目录：

```bash
cp examples/for-blind-app/agent-server/config/generated/ios-phone.local.json \
  examples/for-blind-app/devices/native-ios-phone/RealtimeAgentPhone/Resources/AppConfig.json
```

`AppConfig.json` 的核心字段：

- `server_url`：server 地址，例如 `http://127.0.0.1:8765`。
- `user_id`：同一用户下的多端共享用户编号。
- `device_id`：iOS 端唯一设备编号，不能和 browser-glass、python phone mock、glass playback 重复。
- `auth`：注册鉴权配置，支持 `disabled`、`static_token` 和 `signed_token`。
- `direct_camera_sink_port`：iOS 端本地相机接收 WebSocket 端口，默认 `9001`。
- `audio_input.enabled`：显式启用后，SDK 注册 `realtime_agent.audio_input=sensor.mic`。
- `camera.enabled`：显式启用后，SDK 注册 `sensor.rgb`，并使用 App 注入的 frame source。
- `speaker.enabled`：显式启用后，SDK 注册 `realtime_agent.audio_output=actuator.speaker`。
- `speaker.buffer.*`：SDK 内置 speaker buffer 水位线配置。
- `properties`：声明仅用于日志和 debug 的硬件参数。

缺省情况下音频、相机和 speaker 都是 disabled；只有配置显式 `enabled=true` 时，SDK 才会
注册并维护对应硬件链路。

直连相机接收服务启动后会把 `ws://<iPhone局域网IP>:9001/ws/camera` 写入注册
properties。ESP32 端配置该地址后，可按 `realtime_agent.direct_frame.v1` 推送 JPEG：
4 字节大端 JSON header 长度、JSON header、JPEG payload。header 中使用
`stream_type=sensor.rgb`。iOS phone 不会绕过 server 直接参与对话，只缓存最新帧，
并在收到 server 的 `sensor.rgb` 采集请求时通过 `/ws/stream` 上传。

如果本地启用 signed token：

```bash
uv run realtime-agent.config.sync \
  --auth-mode signed_token \
  --signed-token '<pairing-service-generated-token>' \
  --output-dir examples/for-blind-app/agent-server/config/generated
```

如果只传 `--auth-mode signed_token` 而不传 `--signed-token`，生成配置会保留
`signed_token` 模式并写入提示，提醒开发者先通过配对服务或管理端生成短期 token。

## 启动 server

```bash
# 在项目根目录执行
uv run realtime-agent.server.run --config examples/for-blind-app/agent-server/server.yaml
```

确认 server 可访问：

```bash
curl http://127.0.0.1:8765/api/health
```

## Simulator 运行

直接打开工程：

```bash
open examples/for-blind-app/devices/native-ios-phone/RealtimeAgentPhone.xcodeproj
```

选择 `RealtimeAgentPhone` scheme 和一个 iPhone Simulator 后运行。也可以命令行构建：

```bash
cd examples/for-blind-app/devices/native-ios-phone
xcodebuild -scheme RealtimeAgentPhone -destination 'generic/platform=iOS Simulator' build
```

页面中可执行：

- “连接并注册”：连接 `/ws/control`，发送 `control.device.register.requested`。
- “启动直连相机接收”：打开本机 `9001/ws/camera`，接收 ESP32 推送的 JPEG 帧。
- “上传 sensor.rgb 测试帧”：通过 `/ws/stream` 上传一帧测试 JPEG。
- “上传 sensor.mic 测试 PCM”：通过 `/ws/stream` 上传 20ms 静音 PCM。
- 收到 `actuator.speaker` 下行 chunk 时，由 SDK speaker buffer 维护 started、pause/resume、
  close 和 cancel；页面只显示最近收到的 speaker 字节数。
- 收到 `custom.command.requested` 且 `payload.command=haptic.vibrate` 时，App 通过
  `ctx.emit("custom.haptic.vibrate.done", ...)` 回报业务结果。
- 收到 `custom.navigation.route.updated` 时，App 记录业务事件日志。

## 真机运行

真机运行时，`server_url` 不能使用 iOS 设备自己的 `127.0.0.1`。应改成 Mac 在局域网中的地址，例如：

```json
{
  "server_url": "http://192.168.1.23:8765"
}
```

确认 iPhone、Mac 和 ESP32 在同一局域网，server 监听地址允许局域网访问。首次真机运行需要在
Xcode 中设置 Team 和 Bundle Identifier。本参考端使用 Swift SDK 的 AVFoundation 默认
adapter：`AudioInput.enabled()`、`Camera.enabled()` 和 `Speaker.enabled(...)`。真机运行时需要
允许相机和麦克风权限；蓝牙路由、后台切换和复杂音频会话仍需要真机验证。

## 自动验收

无 Xcode 环境时，至少运行 contract test：

```bash
# 在项目根目录执行
uv run python -m pytest \
  examples/for-blind-app/app-tests/endpoints/test_ios_phone_endpoint_contract.py \
  examples/for-blind-app/app-tests/config/test_endpoint_config_sync.py \
  -q
```

有 Xcode 环境时，再补充运行 iOS build：

```bash
cd examples/for-blind-app/devices/native-ios-phone
xcodebuild -scheme RealtimeAgentPhone -destination 'generic/platform=iOS Simulator' build
```
