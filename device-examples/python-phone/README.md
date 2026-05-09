# python-phone reference endpoint

`python-phone` 是独立网络端侧参考实现，用来验证手机类设备不依赖固定
`phone` 类型建模，也不通过 `device_id` 点对点收发消息。

## 能力边界

默认注册能力通过 `supports` 表达，server 会把它编译成底层 `subscriptions`：

- 生产 `sensor.rgb`；按结构化 supports 声明 `sensor.rgb`，订阅由 server 编译生成
- 消费 `actuator.speaker`、`actuator.haptic`
- 声明 `phone.task.find_object_phone_task` 和 `phone.task.traffic_light_phone_task`
- `subscriptions` 由 server 根据结构化 `supports` 编译，用于接收 `stream.control.*`、`stream.output.*` 和 `command.*`
- 通过 `command.*` 事件回报端侧任务 started / progress /
  completed / failed

真实图片、音频和传感器数据只通过 `/ws/stream` 二进制通道传输；控制事件 payload
只携带 `request_id`、`stream_type`、采样策略和状态。

## 手机视觉任务

phone task 不新增 RPC。server 只发布：

```text
command.requested
```

payload 中包含 `task_type`、`task_id`、输入参数和所需 stream。Python phone mock
通过内置 handler 或 `handler_packages` 自动发现 handler，执行后上报：

```text
command.accepted
command.progress
command.completed
command.failed
```

当前内置 handler：

- `find_object_phone_task`
- `traffic_light_phone_task`

RGB 帧来自 `vision_frames` 配置或默认测试 JPEG，并始终通过 `sensor.rgb` stream
上传。事件日志、任务日志和帧日志会进入运行结果，便于对照真实 iOS 插件行为。

## 视频显示端设计

下一阶段会把 Python 手机端扩展成可长驻运行的视频显示端，用来显示眼镜端传到同一
`user_id` 设备组内的 `sensor.rgb` 视频流，并为后续 YOLO 等本地视觉算法预留扩展点。

设计文档见 [VIDEO_DISPLAY_DESIGN.md](VIDEO_DISPLAY_DESIGN.md)。

本地视频窗口使用 OpenCV 实现。启动后会注册为一台普通设备，订阅 `sensor.rgb`
输入流，并把收到的最近一帧保存到 `runs/audio-chat/python-phone/latest-rgb.jpg`。

```bash
uv run python -m audio_chat_python_phone_mock --config device-examples/python-phone/phone.preview.yaml
```

## 启动

终端 1：

```bash
uv run audio-chat.server.run --config app-examples/for-blind-app/server.yaml
```

终端 2：

```bash
uv run python -m audio_chat_python_phone_mock --config device-examples/python-phone/phone.mock.yaml
```

`mode: register_only` 会完成注册后退出，适合自动验收；后续长驻联调可以把它改成
保持连接的模式。
