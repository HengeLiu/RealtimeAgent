# iOS 准备说明

当前目录尚未生成完整的 Flutter iOS Runner 工程。

在具备 Flutter CLI 和完整 Xcode 的机器上执行：

```bash
cd clients/phone_flutter
flutter create . --platforms=ios
flutter pub get
```

之后在 `ios/Runner/Info.plist` 中补这些权限：

- `NSCameraUsageDescription`
- `NSMicrophoneUsageDescription`
- `NSLocalNetworkUsageDescription`
- `NSPhotoLibraryUsageDescription`

如果后续使用 Bonjour，再补：

- `NSBonjourServices`
