# devices

`devices` 保存 `realtime-agent` 的多语言 Device SDK、端侧协议说明和端侧行为标准。Device SDK 的职责是把真实设备或模拟设备接入 Server SDK：完成注册、心跳、控制事件、媒体 stream、命令回执和基础诊断。

端侧 SDK 不负责业务 Tool / Task，也不替开发者实现具体硬件驱动。麦克风、相机、speaker、震动器、屏幕或其他硬件能力由端侧 App 接入 SDK 暴露的配置、source、sink 和 handler。

## 目录结构

```text
devices/
  docs/          # 端侧 App 接入、事件行为标准和 SDK 实现蓝图
  python/        # Python Device SDK，适合测试、Linux 网关和开发支持端侧
  typescript/    # TypeScript / JavaScript Device SDK，适合浏览器、Node、Electron
  swift/         # Swift Device SDK，适合 iOS / macOS
  kotlin/        # Kotlin / Java Device SDK，适合 Android / JVM
  c/             # C Device SDK，适合 ESP32、嵌入式 Linux 和自定义网络栈
```

`devices/swift-backup/` 是历史阶段备份目录，不作为当前公开接入入口。

## 正式协议通道

所有语言 SDK 应使用同一套正式通道：

| 通道 | 路径 | 用途 |
| --- | --- | --- |
| Control WebSocket | `/ws/control` | 注册、心跳、命令、stream 生命周期事件。 |
| Audio Input WebSocket | `/ws/stream/audio/input?device_id=<device_id>` | 端侧上传 `sensor.mic` PCM chunk。 |
| Audio Output WebSocket | `/ws/stream/audio/output?device_id=<device_id>` | 端侧接收 server 下发的 `actuator.speaker` chunk。 |
| Visual Input WebSocket | `/ws/stream/visual/input?device_id=<device_id>` | server 请求后，端侧上传一帧 `sensor.rgb` 图片。 |

不要再使用旧的单一 `/ws/stream?device_id=...` 作为新实现入口。

## 语言入口

| SDK | 入口 | 当前定位 |
| --- | --- | --- |
| Python | [python](python/README.md) | 基准 SDK、测试 harness、Linux / Python 端侧。 |
| TypeScript | [typescript](typescript/README.md) | 浏览器、Node、Electron 和 WebView 端侧。 |
| Swift | [swift](swift/README.md) | iOS / macOS 端侧，当前真机 demo 的主要 SDK。 |
| Kotlin / Java | [kotlin](kotlin/README.md) | Android / JVM 协议模型和端侧 SDK。 |
| C | [c](c/README.md) | ESP32、嵌入式 Linux 和自定义网络栈的最小协议核心。 |

## 推荐阅读

- [端侧 App 接入指南](docs/device-app-integration.md)
- [设备事件行为标准](docs/device-event-behavior.md)
- [端侧 SDK 事件行为实现蓝图](docs/device-sdk-event-blueprint.md)
- [通讯协议](../protocol/docs/protocol.md)

## 常用测试

```bash
uv run python -m pytest devices/python/unit-tests devices/python/protocol-tests -q
cd devices/typescript && npm test
cd devices/swift && swift test
```

Kotlin 和 C SDK 的测试需要本机具备对应工具链：

```bash
cd devices/kotlin && gradle test
cmake -S devices/c -B devices/c/build
cmake --build devices/c/build
ctest --test-dir devices/c/build --output-on-failure
```
