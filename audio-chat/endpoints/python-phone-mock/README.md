# python-phone-mock reference endpoint

`python-phone-mock` 是独立网络端侧参考实现，用来验证手机类设备不依赖固定
`phone` 类型建模，也不通过 `device_id` 点对点收发消息。

## 能力边界

默认注册能力：

- 生产 `sensor.rgb`、`sensor.depth`、`sensor.imu`
- 消费 `actuator.speaker`、`actuator.haptic`
- 订阅 `stream.control.*` 中和传感器相关的控制事件
- 订阅 `stream.output.*` 中 speaker / haptic 执行器输出

真实图片、音频和传感器数据只通过 `/ws/stream` 二进制通道传输；控制事件 payload
只携带 `request_id`、`stream_type`、采样策略和状态。

## 启动

终端 1：

```bash
uv run audio-chat.server.run --config examples/minimal/server.yaml
```

终端 2：

```bash
uv run audio-chat.phone.mock --config endpoints/python-phone-mock/phone.mock.yaml
```

`mode: register_only` 会完成注册后退出，适合自动验收；后续长驻联调可以把它改成
保持连接的模式。
