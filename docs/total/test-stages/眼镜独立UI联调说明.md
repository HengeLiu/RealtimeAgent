# 眼镜独立 UI 联调说明

## 1. 文档目的

本文档说明当前真实联调方式下，如何通过眼镜服务自身提供的 UI 页面完成样例数据上传、单次录音语音交互和找物任务触发。

当前约束如下：

- 不再保留“测试支持服务”这一独立角色
- `server`、`glass`、`phone` 三端分别独立运行
- 样例数据注入页面由 `glass` 服务直接提供
- 样例输入通过眼镜感知总线进入系统

---

## 2. 当前代码落位

- 眼镜运行时：
  - [app.py](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/nextgen/apps/glass/runtime/app.py)
- 眼镜控制面与 UI：
  - [http_control_app.py](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/nextgen/apps/glass/runtime/http_control_app.py)
- 感知总线：
  - [sensor_hub.py](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/nextgen/apps/glass/sensors/sensor_hub.py)
- 感知输入源：
  - [input_sources.py](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/nextgen/apps/glass/sensors/input_sources.py)

---

## 3. 两类感知输入实现

### 3.1 UI 模拟输入源

作用：

- 接收浏览器上传的文本、图片、视频
- 将这些样例数据作为眼镜传感器输入的模拟来源
- 保存输入记录，便于 UI 页面查看当前注入状态

当前支持：

- 文本注入
- 图片路径注入
- 视频路径注入

### 3.2 真实硬件输入源

作用：

- 接收本机摄像头、麦克风等真实设备输入
- 为后续替换成真正眼镜硬件驱动保留稳定接口

当前状态：

- 已有本机摄像头与麦克风接入能力
- 后续替换成真硬件时只需要替换该输入源内部实现

---

## 4. 启动方式

### 4.1 启动服务器

```bash
cd /Users/elio/dev/llm-project/OpenAIglasses_for_Navigation
uv run python scripts/run_server_control_runtime.py
```

### 4.2 启动眼镜

```bash
cd /Users/elio/dev/llm-project/OpenAIglasses_for_Navigation
uv run python scripts/run_glass_control_runtime.py
```

### 4.3 启动手机

- 在 iPhone 上运行 [clients/phone_flutter](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/clients/phone_flutter)
- `Server Base URL` 指向 `http://<你的Mac局域网IP>:18490`
- `glass` 脚本会自动推断：
  - `advertise-host`：当前 Mac 的局域网 IP
  - `server-base-url`：`http://<当前Mac局域网IP>:18490`

---

## 5. 打开眼镜 UI

浏览器访问：

```text
http://127.0.0.1:18491/
```

页面支持：

- 查看当前三端状态快照
- 查看眼镜整体状态 `READY / RECORDING / PLAYING`
- 创建找物任务级长连接
- 注入文本输入
- 上传图片模拟单帧输入
- 上传视频模拟流式传输
- 控制“开始录音 / 结束录音并发送”的单次语音交互

---

## 6. 当前日志

- server 日志：
  - [server-runtime.log](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/nextgen/apps/server/logs/server-runtime.log)
- glass 日志：
  - [glass-runtime.log](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/nextgen/apps/glass/logs/glass-runtime.log)
- phone 日志：
  - iPhone App 内 `Runtime Logs`

---

## 7. 当前最小联调流程

1. 独立启动 `server`
2. 独立启动 `glass`
3. 在 iPhone 上启动 `phone`
4. 打开眼镜 UI 页面
5. 点击“开始录音”，录制完成后点击“结束录音并发送”
6. 观察服务器日志中的 ASR 文本和眼镜端语音播放
7. 如需找物联调，再点击“创建长连接”
8. 上传图片或视频
9. 观察手机检测、眼镜播报和服务器状态变化

---

## 8. 当前最小语音链路

当前已落地的无图语音交互流程如下：

1. 眼镜启动并注册成功后，麦克风和相机默认关闭，整体状态为 `READY`
2. 用户在眼镜 UI 点击“开始录音”
3. 眼镜启动本机麦克风录音，整体状态切换为 `RECORDING`
4. 用户点击“结束录音并发送”
5. 眼镜停止录音并把音频文件上送服务器
6. 服务器调用 ASR，将完整转写文本打印到日志
7. 服务器使用“转写文本 + 历史 messages”调用大模型
8. 服务器调用 TTS，将音频块通过同一条语音 WebSocket 回传眼镜
9. 眼镜通过执行器总线控制喇叭播放，播放期间状态为 `PLAYING`
10. 播放完成后，眼镜状态回到 `READY`

说明：

- 当前代码中仍保留实时语音会话底层能力，但它不再是眼镜 UI 的主流程
- 当前阶段暂不接图片到自然语言视觉问答链路，后续再补
