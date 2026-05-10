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
# 在项目根目录执行
uv run audio-chat.server.run --config app-examples/for-blind-app/server.yaml
```

打开页面：

```bash
open device-examples/browser-glass/index.html
```

或者使用 CLI：

```bash
uv run audio-chat.web.open --print-url
```

页面会把 Server URL、User ID、Device ID、输入模式和调试事件内容保存到浏览器
`localStorage`。再次打开时优先使用 URL 参数，其次使用上一次保存的值，最后才使用
示例默认值。`device_id` 默认固定为 `dev-browser-glass-001`，不会每次刷新随机变化；
需要切换设备身份时，手动修改页面里的 Device ID 即可。

## 协议口径

页面注册为普通 Device。新的推荐入口是 [device.audio-chat.yaml](device.audio-chat.yaml)，它声明端侧支持的传感器和执行器：

1. `device_id`
2. `user_id`
3. `name`
4. `supports`
5. `properties`

本地校验：

```bash
uv run audio-chat.device.validate device-examples/browser-glass/device.audio-chat.yaml --json
```

校验命令会按 `supports` 生成注册事件所需的内部路由。页面运行时也按同一口径提交 `supports`，server 负责生成业务路由；页面不再手写事件路由。设备收到事件后，通过实际 stream 行为证明自己能生产或消费对应数据。

## 音频测试模式

1. `真实麦克风实时对话`：使用浏览器麦克风持续上传 `sensor.mic`。
2. `离线音频实时注入`：把本地音频按 20ms chunk 发送，模拟真实长连接。
3. `离线音频快速回放`：尽快上传完整音频，只用于复现问题。

离线实时注入支持文件结束后的三种策略：

1. `保持连接并发送静音`
2. `保持连接但暂停发送`
3. `关闭麦克风 stream`

连续对话和 Omni Realtime 测试优先使用“保持连接并发送静音”。
