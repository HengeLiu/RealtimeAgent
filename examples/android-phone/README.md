# AudioChat Phone - Android App

## 📱 项目简介

这是一个完全复刻 Python 端 `phone_mock` 协议的 Android 应用，用于与 OpenAIglassesDemo Server 进行 1:1 协议通信。

## 🎯 功能特性

### ✅ 已实现

| 模块 | 功能 | 状态 |
|------|------|------|
| **协议层** | AudioChatEvent 事件编解码 | ✅ 完成 |
| **协议层** | StreamChunk 二进制流编解码 | ✅ 完成 |
| **网络层** | WebSocket 双通道通信 | ✅ 完成 |
| **设备管理** | 设备注册/注销 | ✅ 完成 |
| **设备管理** | 心跳保持 (10s) | ✅ 完成 |
| **设备管理** | 控制事件处理 | ✅ 完成 |
| **音频** | 音频采集器框架 (16kHz PCM) | ✅ 完成 |
| **音频** | 音频播放器框架 (24kHz) | ✅ 完成 |
| **视频** | CameraX 拍照模块 | ✅ 完成 |
| **UI** | Jetpack Compose 界面 | ✅ 完成 |

### 🔄 待完善

- [ ] 权限请求流程（麦克风、摄像头）
- [ ] 前台服务实现
- [ ] 实际音频采集集成到 DeviceManager
- [ ] 实际摄像头拍照集成到 DeviceManager
- [ ] 震动执行器实现
- [ ] 视觉模型集成（YOLO/YOLOE）

---

## 🏗️ 架构设计

```
┌─────────────────────────────────────────┐
│              UI Layer                   │
│         (Jetpack Compose)              │
├─────────────────────────────────────────┤
│            ViewModel                    │
│        (MainViewModel)                 │
├──────────┬──────────┬──────────────────┤
│   Device │  Audio   │    Video         │
│ Manager  │ Manager  │   Manager        │
├──────────┴──────────┴──────────────────┤
│          Network Layer                 │
│   (AudioChatWebSocketClient)           │
├─────────────────────────────────────────┤
│           Protocol Layer               │
│  AudioChatEvent / StreamChunkCodec     │
└─────────────────────────────────────────┘
```

---

## 📡 协议复刻清单

### 控制事件 (Control WebSocket)

| 事件名 | 方向 | 状态 |
|--------|------|------|
| `control.device.register.requested` | App → Server | ✅ |
| `control.device.registered` | Server → App | ✅ |
| `control.device.heartbeat.received` | App → Server | ✅ |
| `stream.control.open.requested` | Server → App | ✅ |
| `stream.input.opened` | App → Server | ✅ |
| `stream.input.closed` | App → Server | ✅ |
| `command.requested` | Server → App | ✅ |
| `command.accepted` | App → Server | ✅ |
| `command.completed` | App → Server | ✅ |
| `command.failed` | App → Server | ✅ |
| `stream.output.close.requested` | Server → App | ✅ |
| `stream.output.finished` | App → Server | ✅ |
| `stream.output.closed` | App → Server | ✅ |

### 数据流 (Stream WebSocket)

| 流类型 | 方向 | 格式 | 状态 |
|--------|------|------|------|
| `sensor.mic` | App → Server | PCM16LE 16kHz | ⏳ 框架完成 |
| `sensor.rgb` | App → Server | JPEG | ⏳ 框架完成 |
| `actuator.speaker` | Server → App | PCM16LE 24kHz | ⏳ 框架完成 |
| `actuator.haptic` | Server → App | Command | ❌ 待实现 |

---

## 🚀 快速开始

### 环境要求

- **Android Studio**: Hedgehog (2023.1.1) 或更高版本
- **Gradle**: 8.2+
- **minSdk**: 26 (Android 8.0)
- **targetSdk**: 34 (Android 14)
- **Kotlin**: 1.9.20

### 编译步骤

1. **克隆项目**
```bash
cd examples/android-phone
```

2. **用 Android Studio 打开**
   - File → Open → 选择 `examples/android-phone`
   - 等待 Gradle 同步完成

3. **连接真机或模拟器**

4. **运行 App**
   - 点击 Run 按钮 (▶️)
   - 或使用命令行: `./gradlew installDebug`

### 使用步骤

1. **配置服务器地址**
   - 默认: `http://127.0.0.1:8765`
   - 如果在电脑上测试，需要改为电脑的局域网 IP

2. **配置 User ID 和 Device ID**
   - 默认值已预设，可自定义

3. **点击"连接并注册"**
   - 观察日志输出
   - 等待注册成功提示

4. **测试功能**
   - 日志区域会显示所有事件和状态变化

---

## 🌐 局域网测试

如果要在手机上连接电脑上的 Server：

1. **查看电脑 IP**
```bash
ipconfig # Windows
ifconfig # Linux/Mac
```

2. **修改 App 中的 Server URL**
```
http://192.168.x.x:8765  # 替换为实际 IP
```

3. **确保手机和电脑在同一网络**

4. **启动 Server**
```bash
uv run audio-chat.server.run --app-name for-blind-app
```

5. **在 App 中点击连接**

---

## 📁 项目结构

```
examples/android-phone/
├── app/
│   ├── build.gradle.kts          # 应用构建配置
│   └── src/main/
│       ├── AndroidManifest.xml   # 应用清单
│       ├── java/com/audiochat/phone/
│       │   ├── protocol/         # 协议层
│       │   │   ├── AudioChatEvent.kt      # 事件定义
│       │   │   ├── DeviceSupports.kt      # 能力声明
│       │   │   ├── StreamChunk.kt         # 流数据块
│       │   │   └── GsonFactory.kt         # JSON 工具
│       │   ├── network/          # 网络层
│       │   │   └── AudioChatWebSocketClient.kt  # WebSocket 客户端
│       │   ├── device/           # 设备管理层
│       │   │   └── DeviceManager.kt         # 设备管理器
│       │   ├── audio/            # 音频模块
│       │   │   ├── AudioCaptureManager.kt  # 音频采集
│       │   │   └── AudioPlaybackManager.kt # 音频播放
│       │   ├── video/            # 视频模块
│       │   │   └── CameraManager.kt        # 摄像头管理
│       │   ├── ui/              # UI 层
│       │   │   ├── MainActivity.kt          # 主界面
│       │   │   └── MainViewModel.kt         # ViewModel
│       │   └── AudioChatApplication.kt      # Application 类
│       └── res/                  # 资源文件
├── settings.gradle.kts           # 项目设置
└── README.md                     # 本文件
```

---

## 🔧 配置说明

### Server 配置对应关系

| Python 配置项 | Android 对应位置 |
|--------------|-----------------|
| `server_url` | MainViewModel.serverUrl |
| `user_id` | MainViewModel.userId |
| `device_id` | MainViewModel.deviceId |
| `name` | DeviceManager.deviceName |
| `properties` | DeviceManager.properties |
| `supports` | DeviceSupports 类 |

### 音频参数一致性

| 参数 | Server 配置 | Android 实现 |
|------|------------|-------------|
| 麦克风采样率 | 16000 Hz | `AudioCaptureManager.SAMPLE_RATE` |
| 麦克风格式 | pcm16le | `AudioFormat.ENCODING_PCM_16BIT` |
| 麦克风帧长 | 20ms | `CHUNK_MS = 20` |
| 扬声器采样率 | 24000 Hz | `AudioPlaybackManager.SAMPLE_RATE` |
| 扬声器格式 | pcm16le | `AudioFormat.ENCODING_PCM_16BIT` |

---

## 🐛 调试技巧

### 查看 WebSocket 通信

App 的日志面板会显示：
- 连接状态变化
- 收发的事件名称
- 数据传输统计

### Logcat 过滤

```bash
adb logcat -s "AudioChat*" "DeviceManager" "MainViewModel"
```

### 常见问题

**Q: 连接失败？**
- 检查 Server 是否启动：`curl http://127.0.0.1:8765/api/health`
- 检查防火墙设置
- 确保使用正确的 IP 地址（不是 localhost）

**Q: 注册失败？**
- 检查 User ID 和 Device ID 格式
- 查看 Server 日志确认错误原因

**Q: 无法录音？**
- 检查麦克风权限是否授予
- Android 10+ 需要前台服务才能后台录音

---

## 📊 与 Python Mock 对比

| 功能 | Python Mock | Android App |
|------|------------|-------------|
| 设备注册 | ✅ | ✅ |
| 心跳保持 | ✅ | ✅ |
| 控制事件处理 | ✅ | ✅ |
| RGB 图片上传 | ✅ | ✅ (框架完成) |
| 音频流上传 | ✅ | ✅ (框架完成) |
| 音频播放 | ✅ | ✅ (框架完成) |
| 命令执行 | ✅ | ✅ (部分) |
| 视觉处理 (YOLO) | ✅ | ❌ 待移植 |
| Peer Video | ✅ | ❌ 待移植 |
| GUI 显示 | ✅ (OpenCV/PySide6) | ✅ (Compose) |

---

## 📝 开发计划

### Phase 1: 核心功能 ✅
- [x] 协议层实现
- [x] 网络通信
- [x] 设备管理
- [x] 基础 UI

### Phase 2: 完善功能 🔄
- [ ] 权限请求
- [ ] 前台服务
- [ ] 音频完整链路
- [ ] 拍照完整链路

### Phase 3: 高级功能 🔮
- [ ] YOLO 视觉模型
- [ ] Peer Video 支持
- [ ] 后台任务处理
- [ ] 生产级优化

---

## 🤝 贡献指南

欢迎提交 Issue 和 PR！

开发时请遵循：
- 保持与 Python 端协议一致
- 使用 Kotlin 编码规范
- 添加中文注释
- 更新本文档

---

## 📄 许可证

MIT License