# Flutter App 构建说明

这个目录已经补齐为可继续构建的 Flutter Android 工程骨架，包含：

- `lib/` 应用代码
- `android/` Android 宿主工程
- `pubspec.yaml`
- `analysis_options.yaml`

## 本机还需要的环境

要真正导出 APK，还需要本机安装：

- Flutter SDK
- Android SDK / Platform Tools
- 一个可用的 Android API 平台

## 建议构建步骤

安装好 Flutter 后在本目录执行：

```powershell
flutter doctor
flutter pub get
flutter build apk --release
```

默认 APK 输出目录：

```text
build\app\outputs\flutter-apk\app-release.apk
```

## 备注

- `android/local.properties` 不应该提交到仓库，安装好 Flutter 后会自动生成或由你手动填写。
- 如果你想让我继续在这台机器上直接打包，我还需要先把 Flutter SDK 和 Android SDK 安装好。
