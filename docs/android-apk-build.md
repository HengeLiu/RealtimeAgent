# Android APK 构建与安装指南

## 环境要求

- Android Studio 或 Android SDK
- NDK (Native Development Kit)
- Gradle
- adb (Android Debug Bridge)

## 构建 APK

```bash
cd examples/android-phone

# debug 构建
./gradlew assembleDebug

# release 构建
./gradlew assembleRelease
```

APK 输出路径: `app/build/outputs/apk/debug/app-debug.apk`

## 安装到手机

### 方法一：单个设备

```bash
# 查看已连接设备
adb devices

# 安装 APK (覆盖安装)
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

### 方法二：多设备指定

```bash
# 指定设备安装
adb -s <设备ID> install -r app/build/outputs/apk/debug/app-debug.apk

# 示例
adb -s 192.168.31.50:45663 install -r app/build/outputs/apk/debug/app-debug.apk
```

## 常见问题

### 编译失败：缺少 .aar 依赖

如果编译报错找不到 `AliyunAuthManager` 的类，检查 `app/libs/` 目录是否有必要的 .aar 文件：

```bash
ls app/libs/
# 应包含:
# - auth_number_product-*.aar
# - logger-*.aar
# - main-*.aar
```

如缺少，从阿里云认证 SDK 包中复制到 `app/libs/` 目录。

### 多设备冲突

```bash
# 报错: adb: more than one device/emulator

# 解决方案：指定设备
adb -s <设备IP:端口> install -r app/build/outputs/apk/debug/app-debug.apk
```

## 文件说明

| 目录/文件 | 用途 |
|-----------|------|
| `app/libs/` | 本地 AAR 依赖 (阿里云认证 SDK) |
| `app/src/main/jni/` | NCNN/YOLO C++ 源码 |
| `app/src/main/assets/` | NCNN 模型文件 (.bin/.param) |
| `build/` | Gradle 构建输出 |
| `.gradle/` | Gradle 缓存 |
