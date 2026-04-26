# phone-ios

本目录放 iOS 通用手机 SDK 运行时。它负责设备注册、控制连接、视频接收、手机侧任务承载、事件上报和调试回显，不放 `find_object`、导航或其他盲人产品业务能力。

当前仍保留为可直接打开的 Xcode 工程，后续可以继续收敛为 Swift Package 或 XCFramework：

```bash
open openaiglass-sdk/phone-ios/GlassesVideoReceiver.xcodeproj
```

盲人产品的手机宿主说明位于 [../../openaiglass-for-blind/host/phone](../../openaiglass-for-blind/host/phone)，具体业务插件位于 [../../openaiglass-for-blind/capabilities](../../openaiglass-for-blind/capabilities)。
