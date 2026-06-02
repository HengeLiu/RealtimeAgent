# Device Demo

`examples/device_app_demo` 是面向端侧 App 开发者的最小真机 / 浏览器 demo。它演示新的端侧 SDK 接入方式：

- 不手写设备注册 JSON。
- 不手写 `control.device.register.requested`。
- 通过 Swift 或 JavaScript 代码声明设备、能力和硬件。
- 显式启用麦克风、相机和喇叭后，由 SDK 维护音频上行、相机帧上传、speaker 播放 buffer 和下行播放。

## 目录

```text
examples/device_app_demo/
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
uv run realtime-agent.server.run --config examples/device_app_demo/agent-server/server.yaml
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
