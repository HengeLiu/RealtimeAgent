# iOS 准备说明

当前目录已经生成完整的 Flutter iOS Runner 工程。

推荐步骤：

```bash
cd clients/phone_flutter
flutter pub get
open ios/Runner.xcworkspace
```

然后：

1. 在 Xcode 中打开 `Runner.xcworkspace`
2. 选中 `Runner -> Signing & Capabilities`
3. 配置你的 Team 和唯一的 Bundle Identifier
4. 确认已连接 iPhone 真机
5. 首次运行时在手机上信任开发者证书

当前 `ios/Runner/Info.plist` 已补这些权限：

- `NSCameraUsageDescription`
- `NSMicrophoneUsageDescription`
- `NSLocalNetworkUsageDescription`
- `NSPhotoLibraryUsageDescription`

如果后续使用 Bonjour，再补：

- `NSBonjourServices`
