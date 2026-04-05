# iPhone 真机联调操作清单

## 1. 目标

本文档用于指导当前仓库的手机端 Flutter 正式实现部署到 iPhone 真机，并与本机独立运行的 `server`、`glass` 完成联调。

当前仓库状态：

- 手机端正式实现目录为 [clients/phone_flutter](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/clients/phone_flutter)
- Python 手机模拟代码已删除
- 独立的“测试支持服务”已删除
- 样例数据上传、语音模式控制、任务触发由眼镜服务自身 UI 提供

---

## 2. 联调前提

- Mac 与 iPhone 在同一局域网
- iPhone 已通过 USB 连接到 Mac，且已在 Finder / Xcode 中信任
- Mac 已安装 Flutter 和完整 Xcode
- 当前仓库根目录存在可用 Python 环境
- 环境变量 `DASHSCOPE_API_KEY` 已配置

---

## 3. 一次性准备

### 3.1 获取依赖

```bash
cd /Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/clients/phone_flutter
flutter pub get
```

### 3.2 打开 iOS 工程

```bash
cd /Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/clients/phone_flutter
open ios/Runner.xcworkspace
```

### 3.3 在 Xcode 中完成签名

1. 选择 `Runner` target
2. 打开 `Signing & Capabilities`
3. 选择你的 Apple Developer Team
4. 将 `Bundle Identifier` 改成你唯一可用的标识
5. 确认 `Automatically manage signing` 已开启

---

## 4. 启动本机联调环境

### 4.1 启动 server

```bash
cd /Users/elio/dev/llm-project/OpenAIglasses_for_Navigation
uv run python scripts/run_server_control_runtime.py
```

### 4.2 启动 glass

```bash
cd /Users/elio/dev/llm-project/OpenAIglasses_for_Navigation
uv run python scripts/run_glass_control_runtime.py
```

这时 `glass` 会自动推断两个原来需要手动填写的参数：

- `advertise-host`
  - 作用：告诉服务器“其他设备应该用哪个 IP 回连眼镜”
- `server-base-url`
  - 作用：告诉眼镜“服务器控制面地址是什么”

当前默认行为已经改成：

- `advertise-host`：自动取当前 Mac 的局域网 IP
- `server-base-url`：自动拼成 `http://<当前Mac局域网IP>:18490`

### 4.3 打开眼镜 UI 页面

```text
http://127.0.0.1:18491/
```

页面用于：

- 查看三端状态
- 创建找物任务级长连接
- 向眼镜感知总线注入文本、图片、视频
- 控制对讲模式和实时对话模式

---

## 5. 启动 iPhone 真机 App

```bash
cd /Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/clients/phone_flutter
flutter run -d <你的iPhone设备ID>
```

App 中填写：

- `Server Base URL`
  - `http://<你的Mac局域网IP>:18490`
- `Device ID`
  - `phone-001`
- `Local Listen Port`
  - `19092`

然后点击：

- `启动手机端通信壳`

---

## 6. 最小联调闭环

### 6.1 验证注册与心跳

先确认：

- 浏览器访问 [glass UI](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/docs/total/test-stages/眼镜独立UI联调说明.md)
- `server` 状态页：`http://127.0.0.1:18490/status`
- `glass` UI 页面能看到 `phone-001` 已注册

### 6.2 验证找物链路

1. 在眼镜 UI 输入“帮我找一下手机”
2. 点击“创建长连接”
3. 观察 iPhone App 是否收到 `prepare-peer-link`
4. 待长连接建立后，在眼镜 UI 上传图片或视频
5. 观察 iPhone 日志中是否出现 `/stream/frame`
6. 观察眼镜是否收到引导建议并播报

### 6.3 验证语音模式

1. 在眼镜 UI 点击“开始对讲录音”再点击结束
2. 或点击“开始实时对话”
3. 观察 server 是否创建语音会话
4. 观察眼镜是否收到并播放流式 TTS 音频

---

## 7. 日志查看

- server 日志：
  - [server-runtime.log](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/nextgen/apps/server/logs/server-runtime.log)
- glass 日志：
  - [glass-runtime.log](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/nextgen/apps/glass/logs/glass-runtime.log)
- iPhone 日志：
  - App 页面中的 `Runtime Logs`
  - `flutter run` 输出
  - Xcode 控制台

重点关注：

- `register`
- `heartbeat`
- `prepare-peer-link`
- `stream/frame`
- `find-object/frame-analysis`
- `guidance-executed`
