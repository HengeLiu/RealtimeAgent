# iPhone 真机联调操作清单

## 1. 目标

本文档用于指导当前仓库的手机端 Flutter 正式实现部署到 iPhone 真机，并与本机运行的 `server`、`glass`、测试支持服务完成联调。

当前仓库状态：

- 手机端正式实现目录为 [clients/phone_flutter](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/clients/phone_flutter)
- Python 手机模拟代码已删除
- 当前已识别到的 iPhone 设备：
  - `刘恒的 iPhone`
  - device id：`00008130-000579010281001C`

---

## 2. 联调前提

请先确认以下条件：

- Mac 与 iPhone 在同一局域网
- iPhone 已通过 USB 连接到 Mac，且已在 Finder / Xcode 中信任
- Mac 已安装 Flutter 和完整 Xcode
- 当前仓库根目录存在可用 Python 环境
- 环境变量 `DASHSCOPE_API_KEY` 已配置

建议先执行：

```bash
cd /Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/clients/phone_flutter
flutter devices
```

期望看到：

- `刘恒的 iPhone (mobile) • 00008130-000579010281001C`

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

在 Xcode 里依次执行：

1. 选择 `Runner` target
2. 打开 `Signing & Capabilities`
3. 选择你的 Apple Developer Team
4. 将 `Bundle Identifier` 改成你唯一可用的标识，例如：
   - `com.elio.nextgenPhone`
5. 确认 `Automatically manage signing` 已开启

### 3.4 手机上信任开发者

如果是首次运行：

1. 在 iPhone 上接受“是否信任这台电脑”
2. 如果安装后打不开，在手机中前往：
   - `设置 -> 通用 -> VPN 与设备管理`
3. 信任对应开发者证书

---

## 4. 启动本机联调环境

### 4.1 启动 server + glass + 测试支持服务

在仓库根目录执行：

```bash
cd /Users/elio/dev/llm-project/OpenAIglasses_for_Navigation
uv run python scripts/run_local_test_support_service.py
```

默认端口：

- server: `18490`
- glass: `18491`
- test support: `18400`

启动后打开浏览器：

```text
http://127.0.0.1:18400/
```

这个页面用于：

- 查看 server / glass 在线状态
- 向 glass 注入文本、图片、视频
- 观察任务运行状态

### 4.2 确认本机 IP

iPhone 不能用 `127.0.0.1` 访问你电脑上的 server。

请在 Mac 上确认局域网 IP，例如：

```bash
ipconfig getifaddr en0
```

假设输出为：

```text
192.168.31.25
```

则手机端 `Server Base URL` 应填写：

```text
http://192.168.31.25:18490
```

不要填写 `http://127.0.0.1:18490`。

---

## 5. 启动 iPhone 真机 App

### 5.1 用 Flutter 直接运行

```bash
cd /Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/clients/phone_flutter
flutter run -d 00008130-000579010281001C
```

如果你更习惯 Xcode，也可以直接点运行按钮。

### 5.2 在 App 中填写参数

App 页面打开后，按下面填写：

- `Server Base URL`
  - `http://<你的Mac局域网IP>:18490`
- `Device ID`
  - `phone-001`
- `Local Listen Port`
  - `19092`

然后点击：

- `启动手机端通信壳`

期望结果：

- server 状态页能看到 `phone-001`
- App 中 `Runtime Logs` 出现 register / heartbeat 日志
- App 中显示本机 `localHost`

---

## 6. 最小联调闭环

### 6.1 验证注册与心跳

在浏览器打开：

```text
http://127.0.0.1:18400/
```

确认：

- server 在线
- glass 在线
- `phone-001` 已注册

如果需要，也可以直接查看 server 状态页：

```text
http://127.0.0.1:18490/status
```

### 6.2 验证找物任务链路

推荐顺序：

1. 在测试支持页给 glass 发送触发文本，例如“帮我找一下手机”
2. 观察 server 是否创建任务
3. 观察 phone App 是否收到 `prepare-peer-link`
4. 当 glass 与 phone 建立任务级长连接后
5. 通过测试支持页上传图片或视频
6. 观察 phone App 日志中是否出现 `/stream/frame` 或 `/find-object/frame-analysis`
7. 观察 glass 是否收到引导建议并播报

### 6.3 视频流模拟

当页面提示长连接已建立后：

1. 在测试支持页上传视频
2. 页面会按帧送给 glass
3. glass 再通过任务级 WebSocket 转给 phone
4. phone 做找物检测并回传引导

---

## 7. 日志查看

### 7.1 本机服务日志

- server 日志：
  - [server-runtime.log](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/nextgen/apps/server/logs/server-runtime.log)
- glass 日志：
  - [glass-runtime.log](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/nextgen/apps/glass/logs/glass-runtime.log)
- test support 日志：
  - [test-support.log](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/nextgen/integration/test_support/logs/test-support.log)

### 7.2 iPhone 端日志

优先查看：

- App 页面中的 `Runtime Logs`
- `flutter run` 终端输出
- Xcode 控制台输出

重点关注这些关键字：

- `register`
- `heartbeat`
- `prepare-peer-link`
- `peer-link`
- `stream/frame`
- `find-object/frame-analysis`
- `report_task_state`

---

## 8. 常见问题排查

### 8.1 手机连不上 server

先检查：

- `Server Base URL` 是否用了 `127.0.0.1`
- iPhone 和 Mac 是否同网段
- Mac 防火墙是否拦截 Python / Dart / Xcode

### 8.2 App 已启动但 server 看不到 `phone-001`

检查：

- App 页面是否点击了“启动手机端通信壳”
- `Server Base URL` 是否正确
- `http://<Mac-IP>:18490/health` 是否能从手机 Safari 访问

### 8.3 眼镜和手机没有建立任务级连接

检查：

- phone App 的 `Local Listen Port` 是否为 `19092`
- App 是否成功显示 `localHost`
- server 是否向 phone 下发了 `prepare-peer-link`
- glass 日志里是否有连接 phone 的报错

### 8.4 视频上传后没有检测结果

检查：

- 长连接是否已建立
- phone 日志里是否收到 `/stream/frame`
- 上传的视频是否确实包含明显目标
- 当前启用的检测后端是否为 `heuristic`

### 8.5 iPhone 安装成功但打不开

检查：

- 手机是否已信任开发者证书
- Xcode `Signing & Capabilities` 是否完整
- `Bundle Identifier` 是否唯一

---

## 9. 推荐的首次联调顺序

第一次真机联调，建议严格按这个顺序：

1. `flutter devices` 确认真机可见
2. `flutter pub get`
3. Xcode 完成签名
4. 启动 `run_local_test_support_service.py`
5. 浏览器打开测试支持页
6. `flutter run -d 00008130-000579010281001C`
7. App 填 `http://<Mac-IP>:18490`
8. 点击“启动手机端通信壳”
9. 先验证注册与心跳
10. 再验证找物图片
11. 最后验证视频流

---

## 10. 当前可由 Codex 继续协助的部分

我可以继续帮助你做这些事情：

- 根据你本机实际 IP，帮你把联调参数写成固定脚本
- 如果真机运行报错，帮你根据日志定位问题
- 继续把当前启发式检测替换成真正的 iOS 本地模型后端
