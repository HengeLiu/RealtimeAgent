# 项目定位

`audio-chat` 解决的是多设备 AI 应用里重复、复杂、容易出错的 server-side 基础设施问题。

如果一个应用需要同时处理语音输入、模型对话、Tool 调用、后台 Task、摄像头或传感器 stream、设备控制、语音输出和跨设备排障，开发者通常要从零实现设备注册、WebSocket、媒体流、事件路由、播放仲裁和调试产物。`audio-chat` 把这些能力收敛成一个 Python SDK，让开发者主要关注业务能力。

## 它适合什么

`audio-chat` 适合这类应用：

1. 语音优先的 AI Agent 应用。
2. 智能眼镜、手机、浏览器、ESP32 或其他设备共同参与的应用。
3. 需要摄像头、IMU、深度图、震动器、speaker 等端侧能力的应用。
4. 需要把一次性动作做成 Tool，把持续任务做成 Task 的应用。
5. 需要本地回放、mock 设备、运行产物和可复现排障的应用。

典型场景包括：

- 智能眼镜助手。
- 视觉辅助和导航。
- 手机与眼镜协作。
- 浏览器设备原型。
- ESP32 或其他边缘设备接入语音 Agent。
- 多传感器 AI 原型验证。

## 它不是什么

`audio-chat` 不是一个通用聊天 UI 框架，也不是单纯的语音转文字工具。

它不负责：

1. 端侧真实麦克风录音实现。
2. 端侧真实喇叭播放实现。
3. 唤醒词、AEC、摄像头驱动、I2S、硬件固件等端侧细节。
4. 某个具体智能眼镜硬件的完整产品系统。
5. 替代 Pipecat、LiveKit Agents 等实时媒体 Agent 框架。

更准确的定位是：

> `audio-chat` 是一个用于多设备语音 Agent 应用的 server-side Python SDK。它提供设备注册、能力声明、事件路由、stream 传输、Agent Tool/Task、输出仲裁和运行排障产物。

## 核心开发模型

`audio-chat` 把应用拆成三层：

1. **设备层**：端侧设备声明自己支持什么能力，例如 RGB 摄像头、IMU、震动器、speaker。
2. **SDK runtime 层**：server 负责注册、鉴权、事件、stream、资产、Agent、Task、输出和观测。
3. **业务能力层**：开发者实现 Tool / Task，用 Context API 调用设备能力。

业务代码不应该直接操作底层 WebSocket，也不应该硬编码 `device_id`。推荐写法是：

```python
asset = await context.devices.sensors.rgb.one(...)
await context.output.say("已完成分析")
```

## 和普通语音 Agent 的区别

普通语音 Agent 往往关注：

1. 麦克风输入。
2. ASR。
3. LLM。
4. TTS。
5. 播放输出。

`audio-chat` 在此基础上额外关注：

1. 多设备注册和在线状态。
2. 端侧能力声明。
3. 设备传感器和执行器抽象。
4. 大字节 stream 和资产引用。
5. 长时间后台 Task。
6. 运行产物和跨设备联调证据。

这也是它更适合智能眼镜、手机协作、边缘设备和多传感器原型的原因。

