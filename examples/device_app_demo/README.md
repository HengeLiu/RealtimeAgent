# Device Demo

`examples/device_app_demo` 是面向端侧 App 开发者的最小真机、浏览器和嵌入式 demo。当前 Web Chat demo 使用 JavaScript Device SDK，Swift / iOS demo 已用于验证基础音视频对话链路，ESP32-S3 demo 是基于 C Device SDK 的固件参考实现，用于验证联网、注册、媒体链路、相机、WakeNet 和 AEC 边界。

- 不手写设备注册 JSON。
- 不手写 `control.device.register.requested`。
- 通过 Swift、JavaScript 或 C 代码声明设备、能力和硬件。
- 显式启用麦克风、相机和喇叭后，由 SDK 维护音频上行、相机帧上传、speaker 播放 buffer 和下行播放。

## 目录

```text
examples/device_app_demo/
  agent-server/
    server.yaml
  ios/
    DeviceDemo.xcodeproj
    DeviceDemo/
      DeviceDemoApp.swift
      ContentView.swift
      DeviceDemoRuntime.swift
      CameraPreviewController.swift
      Info.plist
  web-chat/
    index.html
    app.js
    styles.css
    package.json
  esp32-s3/
    README.md
    firmware/
```

## 交互流程

1. 首屏只有一个“开始音视频对话”按钮和右上角调试按钮。
2. 点击开始后，App 请求相机和麦克风权限。
3. App 使用代码式 SDK API 创建 `DeviceClient`，并启用：
   - `AudioInput.enabled()`
   - `Camera.enabled(source:)`
   - `Speaker.enabled(buffer:)`
4. SDK 连接 server、发送设备注册事件、维护心跳和 stream WebSocket。
5. 进入对话页后，页面展示相机视频回显窗口和“对话中”音频状态条。
6. 右上角调试按钮可以查看 server 地址、连接状态、诊断计数和最近日志。

## 真机运行

先启动 SDK Demo 专用 server。这个配置只用于验证端侧 SDK 的注册、控制事件、音频上行、单帧相机采集和喇叭下行播放链路，不加载外部业务能力：

```bash
uv run realtime-agent.server.run --config examples/simple-agent-server/server.yaml
```

确认 iPhone 能访问 Mac 的局域网地址，例如：

```text
http://192.168.10.10:8765/api/health
```

打开 Xcode 工程：

```bash
uv run realtime-agent.ios.open
```

这个工程只依赖本仓库的 Swift Device SDK：

```text
examples/device_app_demo/ios/DeviceDemo.xcodeproj
  -> ../../../devices/swift
```

首次启动后，在右上角调试面板里修改 server 地址，然后点击“开始音视频对话”。不要使用外部业务 App 工程来验证 Swift Device SDK。

## 浏览器运行

Web Chat demo 只依赖本仓库的 JavaScript Device SDK：

```text
examples/device_app_demo/web-chat/app.js
  -> /devices/javascript/src/index.js
```

启动本地静态服务：

```bash
cd examples/device_app_demo/web-chat
npm run dev
```

浏览器打开：

```text
http://127.0.0.1:8766/examples/device_app_demo/web-chat/
```

首次启动后，在页面里设置 server 地址，点击“开始音视频对话”，并授权麦克风和相机。Web Chat 页面不直接实现 WebSocket、StreamChunk、浏览器回声抑制或 speaker buffer，这些都由 JavaScript Device SDK 负责。

## ESP32-S3 固件骨架

ESP32-S3 demo 位于 [esp32-s3](esp32-s3/README.md)。它当前已经接入 C Device SDK、ESP-IDF 工程、WebSocket transport、board config 和基础 adapter 文件。默认 mic/speaker 仍是占位实现，避免在未知板卡上硬编码 I2S/PDM 行为；真机完整音视频前需要按实际板卡补齐硬件音频、WakeNet 和 AEC。

基础构建命令：

```bash
cd examples/device_app_demo/esp32-s3/firmware
idf.py set-target esp32s3
idf.py build
```
