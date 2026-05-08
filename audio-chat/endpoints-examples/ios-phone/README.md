# iOS phone reference endpoint

本目录提供一个最小可运行的 iOS phone 参考端。它用于验证 audio-chat 的
`control.device.register.requested`、事件订阅、`/ws/stream` 二进制 stream、
`sensor.rgb` 上传、测试 `sensor.mic` 上传和 `actuator.speaker` 输出消费。

它不是生产 App，也不负责真实录音、播放、唤醒词、AEC 或硬件驱动。端侧只按
event / stream 协议和 server 协作，不新增 RPC，不把媒体字节放入 control event。

## 目录结构

```text
AudioChatPhone.xcodeproj
AudioChatPhone/
  AudioChatPhoneApp.swift
  ContentView.swift
  Core/
    AppConfig.swift
    AudioChatEvent.swift
    StreamChunkCodec.swift
    AudioChatEndpointRuntime.swift
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
cd audio-chat
uv run audio-chat.config.sync \
  --output-dir examples/basic-app/config/generated \
  --server-url http://127.0.0.1:8765 \
  --user-id user-endpoint-001
```

然后把生成的 iOS 配置覆盖到 App 资源目录：

```bash
cp examples/basic-app/config/generated/ios-phone.local.json \
  endpoints-examples/ios-phone/AudioChatPhone/Resources/AppConfig.json
```

`AppConfig.json` 字段语义和其他参考端一致：

- `server_url`：server 地址，例如 `http://127.0.0.1:8765`。
- `user_id`：同一用户下的多端共享用户编号。
- `device_id`：iOS 端唯一设备编号，不能和 web-glass、python phone mock、glass playback 重复。
- `auth`：注册鉴权配置，支持 `disabled`、`static_token` 和 `signed_token`。
- `capabilities`：声明可生产 `sensor.rgb` / `sensor.mic`，可消费 `actuator.speaker` / `actuator.haptic`。
- `subscriptions`：声明订阅 `stream.control.*`、`stream.output.*` 和 `control.audio_session.*`。

如果本地启用 signed token：

```bash
uv run audio-chat.config.sync \
  --auth-mode signed_token \
  --signed-token '<pairing-service-generated-token>' \
  --output-dir examples/basic-app/config/generated
```

如果只传 `--auth-mode signed_token` 而不传 `--signed-token`，生成配置会保留
`signed_token` 模式并写入提示，提醒开发者先通过配对服务或管理端生成短期 token。

## 启动 server

```bash
cd audio-chat
uv run audio-chat.server.run --config examples/minimal/server.yaml
```

确认 server 可访问：

```bash
curl http://127.0.0.1:8765/api/health
```

## Simulator 运行

直接打开工程：

```bash
open endpoints-examples/ios-phone/AudioChatPhone.xcodeproj
```

选择 `AudioChatPhone` scheme 和一个 iPhone Simulator 后运行。也可以命令行构建：

```bash
cd audio-chat/endpoints-examples/ios-phone
xcodebuild -scheme AudioChatPhone -destination 'platform=iOS Simulator,name=iPhone 16' build
```

页面中可执行：

- “连接并注册”：连接 `/ws/control`，发送 `control.device.register.requested`。
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

确认 iPhone 和 Mac 在同一局域网，server 监听地址允许局域网访问。首次真机运行需要在
Xcode 中设置 Team 和 Bundle Identifier。这个参考端只验证协议和 stream，不申请相机、
麦克风或播放器权限；后续接真实硬件能力时也必须保持同一套 event / stream 协议。

## 自动验收

无 Xcode 环境时，至少运行 contract test：

```bash
cd audio-chat
uv run python -m pytest \
  tests/test_ios_phone_endpoint_contract.py \
  tests/test_endpoint_config_sync.py \
  -q
```

有 Xcode 环境时，再补充运行 iOS build：

```bash
cd audio-chat/endpoints-examples/ios-phone
xcodebuild -scheme AudioChatPhone -destination 'platform=iOS Simulator,name=iPhone 16' build
```
