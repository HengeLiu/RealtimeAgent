# Device Demo

`examples/device_demo` 是面向端侧 App 开发者的最小 Swift 真机 demo。它演示新的端侧 SDK 接入方式：

- 不手写设备注册 JSON。
- 不手写 `control.device.register.requested`。
- 通过 Swift 代码声明设备、能力和硬件。
- 显式启用麦克风、相机和喇叭后，由 SDK 维护音频上行、相机帧上传、speaker 播放 buffer 和下行播放。

## 目录

```text
examples/device_demo/
  ios/
    DeviceDemo.xcodeproj
    DeviceDemo/
      DeviceDemoApp.swift
      ContentView.swift
      DeviceDemoRuntime.swift
      CameraPreviewController.swift
      Info.plist
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

先启动 server：

```bash
uv run realtime-agent.server.run --config examples/for-blind-app/agent-server/server.yaml
```

确认 iPhone 能访问 Mac 的局域网地址，例如：

```text
http://192.168.10.10:8765/api/health
```

打开 Xcode 工程：

```bash
open examples/device_demo/ios/DeviceDemo.xcodeproj
```

首次启动后，在右上角调试面板里修改 server 地址，然后点击“开始音视频对话”。
