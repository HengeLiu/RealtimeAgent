# iOS phone reference endpoint

本目录暂时冻结 iOS 参考端侧的协议边界，不把 iOS App 当作 Python server SDK 的
发布内容。第一阶段只要求端侧实现遵守同一组配置字段：

- `server_url`
- `user_id`
- `device_id`
- `auth`
- `capabilities`
- `subscriptions`

iOS 端应通过 `/ws/control` 注册设备、接收控制事件，通过 `/ws/stream` 上传
`sensor.rgb` 或消费 `actuator.speaker` / `actuator.haptic`。不得新增 RPC 或把媒体
字节放入控制事件 payload。
