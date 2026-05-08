# browser-glass

`browser-glass` 是 audio-chat 的浏览器设备示例，用来承担开发测试中的感知和执行角色。它不是协议类型，也不要求开发者真实设备使用浏览器实现。

## 主要用途

1. 手动注册 Device。
2. 手动发送 wake / interrupt / close / heartbeat 等控制事件。
3. 使用真实麦克风进行全链路实时对话。
4. 上传 WAV / PCM 文件，按真实时间模拟 `sensor.mic` 长连接。
5. 快速回放离线音频，用于复现 server 或 Agent Core 问题。
6. 使用摄像头、图片或视频文件响应 `sensor.rgb` 请求。
7. 播放 server 下发的 `actuator.speaker` 音频。
8. 模拟 `actuator.haptic` 执行器。

## 启动

先启动 server：

```bash
cd audio-chat
uv run audio-chat.server.run --config app-examples/basic-app/server.yaml
```

打开页面：

```bash
open device-examples/browser-glass/index.html
```

或者使用 CLI：

```bash
uv run audio-chat.web.open --print-url
```

## 协议口径

页面注册为普通 Device，只声明：

1. `device_id`
2. `user_id`
3. `name`
4. `subscriptions`
5. `properties`

页面不依赖旧能力字段做路由。设备收到事件后，通过实际 stream 行为证明自己能生产或消费对应数据。

## 音频测试模式

1. `真实麦克风实时对话`：使用浏览器麦克风持续上传 `sensor.mic`。
2. `离线音频实时注入`：把本地音频按 20ms chunk 发送，模拟真实长连接。
3. `离线音频快速回放`：尽快上传完整音频，只用于复现问题。

离线实时注入支持文件结束后的三种策略：

1. `保持连接并发送静音`
2. `保持连接但暂停发送`
3. `关闭麦克风 stream`

连续对话和 Omni Realtime 测试优先使用“保持连接并发送静音”。
