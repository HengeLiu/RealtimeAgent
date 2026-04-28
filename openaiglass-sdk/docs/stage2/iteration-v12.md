# iteration-v12：端侧 SDK 打包形态

## 本轮目标

补齐 iOS 和 ESP32 SDK 的源码包形态，使 SDK 开发者和功能开发者可以通过统一 package-check 判断三端 SDK 发布输入是否齐全。

## 主要改动

1. 为 iOS 运行时新增 `phone-ios/package-manifest.json`，声明包名、版本、包形态、最低 iOS/Swift 版本、Xcode 工程、运行时代码、测试代码、资源文件和公开能力。
2. 为 ESP32 运行时新增 `glass-esp32/component-manifest.json`，声明包名、版本、包形态、ESP-IDF 目标、最低 ESP-IDF 版本、工程文件、组件文件、托管依赖和公开能力。
3. 扩展 `openaiglass.sdk.package-check`，在 Python wheel 构建和导入检查之外，继续校验 iOS 与 ESP32 清单和文件完整性。
4. 增加单元测试覆盖清单完整性和缺字段错误。
5. 更新 SDK 使用指南，说明 `sdk-v13` 的端侧源码包边界和仍未覆盖的二进制发布能力。

## 当前边界

本轮不把 iOS 运行时改造成 Swift Package 或 XCFramework，也不把 ESP32 工程发布成 ESP-IDF component registry 组件。当前目标是提供稳定的源码集成清单和自动检查入口，避免业务团队复制 SDK 运行时代码或误用构建产物。
