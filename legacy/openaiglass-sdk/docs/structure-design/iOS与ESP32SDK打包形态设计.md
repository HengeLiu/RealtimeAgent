# iOS 与 ESP32 SDK 打包形态设计

## 背景

SDK 已经把服务端 Python、iOS 手机运行时和 ESP32 眼镜运行时放在 `openaiglass-sdk` 下，但端侧目录过去只有可直接打开的工程，缺少稳定的发布输入说明和自动检查入口。功能开发团队在集成时无法判断哪些文件属于 SDK 运行时、哪些文件只是构建产物或业务宿主差异。

`sdk-v13` 先补齐“源码包形态”的最小闭环：不强行把 iOS 改成 Swift Package，也不强行把 ESP32 改成 registry component，而是为当前真实可用工程加上包清单和 package-check 校验。

## 设计目标

1. 端侧 SDK 有明确的包名、版本、包形态、最低平台版本和公开能力。
2. `openaiglass.sdk.package-check` 能同时检查 Python wheel、iOS 源码包和 ESP32 源码工程。
3. 清单只描述 SDK 通用运行时，不包含盲人业务能力代码。
4. 当前形态能服务内部迭代和业务侧源码集成，并为后续 XCFramework、Swift Package、ESP-IDF component registry 发布留下边界。

## iOS 源码包形态

清单文件位于：

```text
openaiglass-sdk/phone-ios/package-manifest.json
```

当前包形态为 `xcode-source-runtime`，包含：

- `GlassesVideoReceiver.xcodeproj`
- 通用手机运行时代码
- SDK 侧测试代码
- `AppConfig.plist` 和 `Info.plist`
- 公开能力声明，例如设备注册、心跳、视频接收、手机任务启动停止、任务事件上报和多能力注册

这个形态适合内部源码集成和快速调试。它不等价于二进制 SDK，因为当前目录仍包含 App target、宿主页面和资源配置。后续二进制发布需要先拆出 framework target，并整理稳定 `public` Swift API。

## ESP32 源码工程形态

清单文件位于：

```text
openaiglass-sdk/glass-esp32/component-manifest.json
```

当前包形态为 `esp-idf-source-project`，包含：

- ESP-IDF 工程入口
- 默认 sdkconfig 和分区表
- `main` 组件源码、CMake 配置和 Kconfig
- 托管依赖声明
- 公开能力声明，例如 WiFi、控制通道、心跳、音频上传、抓拍、推流和通知播放

这个形态适合内部源码集成和真机固件构建。它不等价于发布到 ESP-IDF component registry 的组件，因为硬件配置、宿主工程入口和运行时主体仍在同一个工程中。

## 校验方式

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run openaiglass.sdk.package-check --repo-root .
```

检查通过后，报告中会包含：

- `ios_package`
- `esp32_package`

如果清单缺字段、清单 JSON 无法解析，或清单声明的文件不存在，检查会失败并输出明确错误。

## 后续边界

源码包形态解决“当前 SDK 发布输入是否齐全”的问题，不解决以下问题：

1. iOS 二进制 XCFramework 发布。
2. Swift Package `binaryTarget` checksum 和 artifact 托管。
3. ESP32 独立组件仓库发布。
4. 多硬件型号的 ESP32 配置矩阵。

这些能力应在 SDK 后续版本中继续推进，业务功能团队不应在业务代码中复制端侧运行时。
