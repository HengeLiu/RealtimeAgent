# iOS phone reference endpoint

本目录提供一个最小可运行的 iOS phone 参考端。它用于验证 realtime-agent 的
`control.device.register.requested`、事件订阅、`/ws/stream` 二进制 stream、
`sensor.rgb` 上传、测试 `sensor.mic` 上传和 `actuator.speaker` 输出消费。
同时，它内置一个本地相机 WebSocket 接收服务，用于接收 ESP32 端直连推送的
JPEG 帧，并在 server 请求 `sensor.rgb` 时优先上传最近一帧。

它不是生产 App，也不负责真实录音、播放、唤醒词、AEC 或硬件驱动。端侧只按
event / stream 协议和 server 协作，不新增 RPC，不把媒体字节放入 control event。

## 目录结构

```text
RealtimeAgentPhone.xcodeproj
RealtimeAgentPhone/
  RealtimeAgentPhoneApp.swift
  ContentView.swift
  Core/
    AppConfig.swift
    RealtimeAgentEvent.swift
    StreamChunkCodec.swift
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

## 配置

App 启动时优先读取 bundle 内的 `AppConfig.json`，找不到时读取
`AppConfig.example.json`，再找不到才使用 Swift 代码中的本地默认配置。

推荐先从仓库根目录生成本地配置：

```bash
# 在项目根目录执行
uv run realtime-agent.config.sync \
  --output-dir examples/for-blind-app/audio-server/config/generated \
  --server-url http://127.0.0.1:8765 \
  --user-id user-endpoint-001
```

然后把生成的 iOS 配置覆盖到 App 资源目录：

```bash
cp examples/for-blind-app/audio-server/config/generated/ios-phone.local.json \
  examples/for-blind-app/devices/native-ios-phone/RealtimeAgentPhone/Resources/AppConfig.json
```

`AppConfig.json` 字段语义和其他参考端一致：

- `server_url`：server 地址，例如 `http://127.0.0.1:8765`。
- `user_id`：同一用户下的多端共享用户编号。
- `device_id`：iOS 端唯一设备编号，不能和 browser-glass、python phone mock、glass playback 重复。
- `auth`：注册鉴权配置，支持 `disabled`、`static_token` 和 `signed_token`。
- `direct_camera_sink_port`：iOS 端本地相机接收 WebSocket 端口，默认 `9001`。
- `supports`：推荐的设备语义能力声明，例如 `sensor.rgb`、`sensor.imu`、`actuator.vibrator`；server 会编译成底层订阅。
- `properties`：声明仅用于日志和 debug 的硬件参数。

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
  --output-dir examples/for-blind-app/audio-server/config/generated
```

如果只传 `--auth-mode signed_token` 而不传 `--signed-token`，生成配置会保留
`signed_token` 模式并写入提示，提醒开发者先通过配对服务或管理端生成短期 token。

## 启动 server

```bash
# 在项目根目录执行
uv run realtime-agent.server.run --config examples/for-blind-app/audio-server/server.yaml
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
xcodebuild -scheme RealtimeAgentPhone -destination 'platform=iOS Simulator,name=iPhone 16' build
```

页面中可执行：

- “连接并注册”：连接 `/ws/control`，发送 `control.device.register.requested`。
- “启动直连相机接收”：打开本机 `9001/ws/camera`，接收 ESP32 推送的 JPEG 帧。
- “上传 sensor.rgb 测试帧”：通过 `/ws/stream` 上传一帧测试 JPEG。
- “上传 sensor.mic 测试 PCM”：通过 `/ws/stream` 上传 20ms 静音 PCM。
- 收到 `actuator.speaker` 下行 chunk 时，先写入本地 buffer，再通过 control event 上报 `stream.output.started`、`stream.output.finished` 和 `stream.output.closed`。

## 真机运行

真机运行时，`server_url` 不能使用 iOS 设备自己的 `127.0.0.1`。应改成 Mac 在局域网中的地址，例如：

```json
{
  "server_url": "http://192.168.1.23:8765"
}
```

确认 iPhone、Mac 和 ESP32 在同一局域网，server 监听地址允许局域网访问。首次真机运行需要在
Xcode 中设置 Team 和 Bundle Identifier。这个参考端默认不申请 iPhone 相机、麦克风或
播放器权限；直连相机接收只接收外部设备推送的 JPEG。后续接真实硬件能力时也必须保持
同一套 event / stream 协议。

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
xcodebuild -scheme RealtimeAgentPhone -destination 'platform=iOS Simulator,name=iPhone 16' build
```
