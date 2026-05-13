# browser-glass

`browser-glass` 是 audio-chat 的浏览器设备示例，用来承担开发测试中的感知和执行角色。它不是协议类型，也不要求开发者真实设备使用浏览器实现。

## 主要用途

1. 手动注册 Device。
2. 手动发送 wake / interrupt / close / heartbeat 等控制事件。
3. 使用真实麦克风进行全链路实时对话。
4. 上传 WAV / PCM 文件，按真实时间模拟 `sensor.mic` 长连接。
5. 使用摄像头或图片样例响应 `sensor.rgb` 请求，也可以手动上传图片触发回显测试。
6. 播放 server 下发的 `actuator.speaker` 音频。
7. 模拟 `actuator.haptic` 执行器。
8. 收到 `peer.video.sender.start` 后连接 Python phone receiver，并按 fps 发送 JPEG 帧。

## 启动

先启动 server：

```bash
# 在项目根目录执行
uv run audio-chat.server.run --config examples/for-blind-app/audio-server/server.yaml
```

打开页面：

```bash
open examples/dev-support/devices/browser-glass/index.html
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
uv run audio-chat.device.validate examples/dev-support/devices/browser-glass/device.audio-chat.yaml --json
```

校验命令会按 `supports` 生成注册事件所需的内部路由。页面运行时也按同一口径提交 `supports`，server 负责生成业务路由；页面不再手写事件路由。设备收到事件后，通过实际 stream 行为证明自己能生产或消费对应数据。

## peer video sender

for-blind-app 的找物和红绿灯 Task 会先启动 Python phone receiver，再向 browser-glass 下发：

```text
command.requested command=peer.video.sender.start
```

页面处理流程：

1. 发送 `command.accepted`。
2. 读取 `params.receiver.url` 并建立 WebSocket。
3. 上报 `peer.sender.connecting` 和 `peer.sender.connected`。
4. 按 `params.source.fps` 从摄像头抽帧；如果已经选择图片样例，则循环发送图片样例。
5. 收到 `peer.video.sender.start.stop` 后停止定时器并关闭 WebSocket。

本地日志会打印 `peer.video.sender.start`、`peer.sender.connected`、`peer.video.frame.sent` 和 `peer.video.sender.stop`，用于和 phone 日志、server runs 对齐。

## 音频测试模式

1. `真实麦克风实时对话`：使用浏览器麦克风持续上传 `sensor.mic`。
2. `离线音频实时注入`：把本地音频按 20ms chunk 发送，模拟真实长连接。

离线实时注入会在文件末尾追加短静音尾巴，发送 final chunk 后进入可继续上传下一段的暂停状态。快速批量回放和 CI 验收不由 browser-glass 承担，后续由 `python-playback-glass` 负责。

## 图片回显测试

`browser-glass` 可以和 `python-phone` 视频显示端配合做本地图片回显测试：

1. 启动 server。
2. 启动 `python-phone` 视频显示端。
3. 打开 `browser-glass` 页面并连接注册。
4. 在“带图输入”区域选择图片，点击“上传所选图片”。

图片会以 `sensor.rgb` 输入流上传到 server，server 再转发给同一 `user_id` 下声明
`endpoint.role.visual_display` 或 `actuator.display.rgb` 的显示设备。
