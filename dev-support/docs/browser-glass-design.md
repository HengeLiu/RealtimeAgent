# Browser Glass 设计文档

更新时间：2026-05-15

文档状态：浏览器眼镜模拟组件设计文档。本文以当前
`dev-support/devices/browser-glass` 的实现为准，说明它作为开发/测试支持组件
在联调链路中的职责和边界。录制式系统测试的自动化回放组件，以
[录制式系统集成测试设计](system-test-recording-design.md) 中的 `python-playback-glass` 为准。

## 1. 定位

`browser-glass` 是运行在浏览器里的交互式眼镜模拟组件。它属于开发/测试支持组件：
在协议层注册为普通 Device，用来模拟眼镜侧的麦克风、RGB、speaker 和 peer video sender；
它不是 SDK 协议类型，也不是开发者必须采用的正式设备形态，更不是自动化 CI 主入口。

它承担：

1. 以开发支持组件身份手动注册 Device。
2. 手动触发 wake / interrupt / close。
3. 使用真实麦克风上传 `sensor.mic`。
4. 使用离线音频样例按真实时间节奏上传 `sensor.mic`。
5. 在 server 请求 `sensor.rgb` 时上传图片样例或摄像头抓拍。
6. 播放 server 下发的 `actuator.speaker`。
7. 模拟 `actuator.haptic` 的开始、完成和关闭回执。
8. 收到 `peer.video.sender.start` 后连接 Python phone receiver，并按 fps 发送 JPEG 帧。
9. 展示运行日志和广播事件，帮助开发者观察真实协议链路。

它不承担：

1. server SDK 内部功能。
2. Agent Core、Tool 的业务逻辑。
3. 自动化批量回放和 CI 主入口。
4. 直接生成最终系统测试 Case。

`browser-glass` 可以辅助录制 Case：它记录用户选择的音频和图片样例，后续生成
`python-playback-glass record` 命令或元数据。真正从 runs 产物归纳 Case、自动回放
Case 的职责属于 `python-playback-glass`。

## 2. 当前目录

```text
dev-support/devices/browser-glass/
  README.md
  browser-glass.yaml
  device.realtime-agent.yaml
  index.html
```

当前实现是单页 HTML。后续如果继续扩展，可以再拆成 `src/` 模块；但当前文档和测试应以 `index.html` 的真实行为为准。

## 3. 注册和能力声明

页面注册为普通 Device。注册 payload 使用结构化 `supports`，不手写业务事件路由。

当前页面实际声明：

```yaml
supports:
  sensors:
    - type: rgb
      modes: [single, continuous]
  actuators:
    - type: vibrator
      commands: [vibrate]
properties:
  realtime_agent.audio_input: sensor.mic
  realtime_agent.audio_output: actuator.speaker
  audio.aec: browser_webrtc
  debug.manual_events: true
  debug.file_upload: true
```

说明：

1. 系统麦克风和扬声器是主音频链路，不作为普通 `supports.sensors/actuators` 声明。
2. 当前 browser-glass 实现了 `sensor.rgb`，未实现 IMU 和 ToF 上传。
3. 当前 browser-glass 实现了 haptic 回执模拟，但不真实驱动硬件。
4. server 根据 `supports` 编译路由；设备收到事件后通过实际 stream 行为证明能力。

## 4. 连接模型

`browser-glass` 使用两类 WebSocket：

1. `/ws/control`：设备级常驻控制连接。
2. `/ws/stream?device_id=...`：数据流连接，按需打开。

当前实现不会在注册成功后立刻打开 stream WebSocket。它会在开始音频、响应 `sensor.rgb` 请求或接收 speaker 输出前，通过 `ensureStreamSocketOpen()` 按需打开。

基础流程：

1. 用户点击“连接并注册”。
2. 页面连接 `/ws/control`。
3. 页面发送 `control.device.register.requested`。
4. 收到 `control.device.registered` 后启用 wake 等控制按钮。
5. 用户点击“模拟唤醒”或开启自动唤醒。
6. server 下发 `control.audio_session.open.requested`。
7. 页面回复 `control.audio_session.opened`，进入会话状态。
8. 用户开始音频，页面打开 `/ws/stream` 并上传 `sensor.mic`。
9. server 下发输出或传感器请求时，页面按协议响应。
10. server 下发 `control.audio_session.close.requested` 后，页面回复 `control.audio_session.closed` 并关闭 stream WebSocket。

## 5. 音频输入

当前支持两种模式：

1. `真实麦克风实时对话`
2. `离线音频实时注入`

不再暴露“离线音频快速回放”。快速回放适合自动化复现，后续应由 `python-playback-glass` 承担。

### 5.1 真实麦克风实时对话

页面使用浏览器麦克风：

```js
navigator.mediaDevices.getUserMedia({
  audio: {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true
  }
})
```

上传格式：

```text
stream_type: sensor.mic
codec: pcm16le
sample_rate: 16000
channels: 1
chunk_ms: 20
```

主要流程：

1. 会话打开后，用户点击“开始音频”。
2. 页面发送 `stream.input.opened(sensor.mic)`。
3. 页面持续上传 20ms PCM chunk。
4. 用户点击“停止音频”时，只关闭当前 `sensor.mic`，不结束对话。
5. 用户点击“结束连续对话”时，发送 `control.user.dialog.close.requested`。

### 5.2 离线音频实时注入

离线音频用于用样例文件模拟真实麦克风输入。当前入口是“从样例目录选择音频”，优先使用 File System Access API；不支持该 API 时退回隐藏文件 input。

当前行为：

1. 支持 WAV / PCM。
2. WAV 由浏览器解码后重采样为 16kHz 单声道 PCM16。
3. 按 20ms chunk 定时发送。
4. 文件结束后追加短静音尾巴，帮助服务端 VAD 得到稳定回合边界。
5. 发送 final chunk 后进入 `offline_paused`，对话窗口保持可继续上传下一段。
6. 如果 server 主动关闭 `sensor.mic`，页面清理本地 stream 状态，下一段会新建 stream。

## 6. 图片输入

当前 `sensor.rgb` 只在 server 请求时上传，不提供手动“立即上传图片”按钮。页面只提供
“选择图片”和“选择视频”两个资源选择按钮；真正上传由 server 下发的
`stream.control.open.requested` 或 `peer.video.sender.start` 触发。

数据源优先级：

1. 已选择的图片样例。
2. 隐藏文件 input 中的图片。
3. 浏览器摄像头抓拍。

流程：

1. server 下发 `stream.control.open.requested`，`stream_type=sensor.rgb`。
2. 页面打开或复用 stream WebSocket。
3. 页面发送 `stream.input.opened(sensor.rgb)`。
4. 页面上传一张或多张 JPEG chunk。
5. 页面发送 `stream.input.closed(sensor.rgb)`。

当前实现会把图片压缩到较小 JPEG，避免控制面或 stream 面传输过大的图片。

带 `request_id` 的 `sensor.rgb` 输入流代表单资产采样，例如 realtime visual sampler
在用户说话期间给模型追加当前画面。server 会把这类流写入资产服务，但不会把它们转发给
Python phone 等显示设备。只有不带 `request_id` 的普通连续 RGB stream，或下方
peer video 任务流，才会进入端侧显示 / 处理设备。

## 6.1 peer video 任务发送

找物和红绿灯后台 Tool 不通过 `sensor.rgb` 单资产流把视频转发给手机。当前流程是：

1. 模型明确调用 `find_object 工具` 或 `traffic_light 工具`。
2. server 先向 Python phone 下发 `peer.video.receiver.start`。
3. phone 回报 `peer.receiver.ready`，并提供 receiver WebSocket URL。
4. server 再向 browser-glass 下发 `peer.video.sender.start`。
5. browser-glass 连接 phone receiver，按 fps 抽取图片、视频或摄像头当前帧，发送 JPEG。
6. 收到 `peer.video.sender.start.stop`、控制连接关闭、页面关闭或 WebSocket 异常时停止发送。

这个顺序保证 后台 Tool 之前眼镜不会把视频帧发送到手机；说话期间的 realtime visual sampler
只服务模型视觉输入，不建立眼镜到手机的直连。

## 7. 输出和执行器

### 7.1 speaker

页面接收 `actuator.speaker` chunk 后使用 Web Audio 播放 PCM16。

回执策略：

1. 第一段音频 chunk 到达时发送 `stream.output.started`。
2. 收到 `stream.output.close.requested` 后，不立即关闭。
3. 等待本地播放队列清空后发送 `stream.output.finished` 和 `stream.output.closed`。
4. 如果用户打断，页面停止本地播放并发送关闭回执。

### 7.2 haptic

当前只做协议级模拟：

1. 收到 `stream.output.start.requested` 且 `stream_type=actuator.haptic`。
2. 立即发送 `stream.output.started`。
3. 立即发送 `stream.output.finished`。
4. 立即发送 `stream.output.closed`。

## 8. 并行 stream 状态

当前页面维护 `streamStates`，允许音频和 RGB stream 并行存在。关闭 `sensor.rgb` 不应影响 `sensor.mic`。

主要状态字段：

1. `stream_id`
2. `stream_type`
3. `state`
4. `seq`
5. `bytes_sent`
6. `bytes_received`
7. `opened_at`
8. `close_reason`

当前 UI 展示：

1. control / stream 连接状态。
2. 当前 session 和音频状态。
3. `sensor.mic` seq 和字节数。
4. 最新 `sensor.rgb` seq 和字节数。
5. 运行日志。
6. 广播事件日志。

## 9. 与录制式系统测试的关系

`browser-glass` 负责“手动探索和观察”，`python-playback-glass` 负责“自动回放和验收”。

推荐链路：

1. 开发者用 `browser-glass` 手动跑通一次真实链路。
2. server 写出 runs 产物。
3. `python-playback-glass record` 读取 runs 产物，生成 Case 草稿。
4. 开发者确认 Case 断言。
5. `python-playback-glass run` 自动回放 Case。

因此 `browser-glass` 不应该直接承担批量回放、pytest 入口或 server 内部断言逻辑。

## 10. 验收口径

当前实现应满足：

1. 使用固定默认 `device_id=dev-browser-glass-001`，刷新页面不会随机变化。
2. 表单配置会保存到 `localStorage`。
3. 注册 payload 使用结构化 `supports`。
4. 注册成功后不提前打开 stream WebSocket。
5. 真实麦克风可上传 `sensor.mic`。
6. 离线音频按 20ms chunk 实时注入。
7. 服务端关闭 `sensor.mic` 后，下一段音频会新建 stream。
8. server 请求 `sensor.rgb` 时，页面能上传图片样例或摄像头抓拍。
9. speaker 输出不会因为 close 信令早到而提前关闭。
10. 关闭 `sensor.rgb` 不影响 `sensor.mic`。
11. 页面底部展示运行日志和广播事件日志。
