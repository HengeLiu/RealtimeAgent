# python-phone reference endpoint

`python-phone` 是独立网络端侧参考实现，用来验证手机类设备不依赖固定
`phone` 类型建模，也不通过 `device_id` 点对点收发消息。

## 能力边界

默认注册能力：

- 生产 `sensor.rgb`、`sensor.depth`、`sensor.imu`
- 消费 `actuator.speaker`、`actuator.haptic`
- 声明 `phone.task.find_object_phone_task` 和 `phone.task.traffic_light_phone_task`
- 订阅 `stream.control.*` 中和传感器相关的控制事件
- 订阅 `stream.output.*` 中 speaker / haptic 执行器输出
- 订阅 `control.device.command.*`，用事件回报端侧任务 started / progress /
  completed / failed

真实图片、音频和传感器数据只通过 `/ws/stream` 二进制通道传输；控制事件 payload
只携带 `request_id`、`stream_type`、采样策略和状态。

## 手机视觉任务

phone task 不新增 RPC。server 只发布：

```text
control.device.command.requested
```

payload 中包含 `task_type`、`task_id`、输入参数和所需 stream。Python phone mock
通过内置 handler 或 `handler_packages` 自动发现 handler，执行后上报：

```text
control.device.command.started
control.device.command.progress
control.device.command.completed
control.device.command.failed
```

当前内置 handler：

- `find_object_phone_task`
- `traffic_light_phone_task`

RGB 帧来自 `vision_frames` 配置或默认测试 JPEG，并始终通过 `sensor.rgb` stream
上传。事件日志、任务日志和帧日志会进入运行结果，便于对照真实 iOS 插件行为。

## 启动

终端 1：

```bash
uv run audio-chat.server.run --config app-examples/basic-app/server.yaml
```

终端 2：

```bash
uv run audio-chat.phone.mock --config device-examples/python-phone/phone.mock.yaml
```

`mode: register_only` 会完成注册后退出，适合自动验收；后续长驻联调可以把它改成
保持连接的模式。
