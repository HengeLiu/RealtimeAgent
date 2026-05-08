# phone-ios

本目录放 iOS 通用手机 SDK 运行时。它负责设备注册、控制连接、视频接收、手机侧任务承载、事件上报和调试回显，不放 `find_object`、导航或其他盲人产品业务能力。

当前仍保留为可直接打开的 Xcode 工程，`sdk-v13` 起额外提供 `package-manifest.json` 作为源码包清单。清单声明当前可发布输入，包括 Xcode 工程、运行时代码、测试代码、资源文件和公开能力；`openaiglass.sdk.package-check` 会检查这些文件是否齐全。

```bash
open openaiglass-sdk/phone-ios/GlassesVideoReceiver.xcodeproj
```

当前包形态是 `xcode-source-runtime`，用于内部源码集成和版本检查，不是二进制 XCFramework，也不是 package-first Swift Package。后续如果要让业务方完全不下载 SDK 源码，应先把通用运行时代码拆成 framework target，再生成 XCFramework 或 Swift Package `binaryTarget`。

盲人产品的手机宿主说明位于 [../../openaiglass-for-blind/host/phone](../../openaiglass-for-blind/host/phone)，具体业务插件位于 [../../openaiglass-for-blind/capabilities](../../openaiglass-for-blind/capabilities)。

## 手机能力注册

iOS 通用运行时支持多个业务能力同时接入。业务插件按服务端下发的 `task_type` 注册：

```swift
PhoneCapabilityBootstrap.registerInstaller {
    PhoneTaskCapabilityRegistry.register(taskType: "demo_phone_task") {
        DemoPhoneCapabilityRuntime()
    }
}
```

App 启动时执行 `PhoneCapabilityBootstrap.applyRegisteredInstallers()` 后，`CameraStreamStore` 会使用 `PhoneTaskCapabilityRegistry` 创建组合运行时，并按 `taskType` 分发 `startTask`、`stopTask` 和视频帧。

`PhoneCapabilityRuntimeFactory.register { ... }` 仅保留为旧式单能力接入兼容入口。新业务能力不要使用该入口，否则多个插件同时注册时无法表达各自负责的任务类型。
