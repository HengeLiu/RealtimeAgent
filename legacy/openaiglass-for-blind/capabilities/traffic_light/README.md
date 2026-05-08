# traffic_light

红绿灯识别能力用于辅助用户判断当前过街信号。当前版本严格使用 SDK 公开扩展面实现：

1. 服务端 `StartTrafficLightTool` 创建 `traffic_light_task`。
2. `TrafficLightTask` 通过 `DeviceGroupContext.start_phone_video_link()` 启动眼镜到手机的视频链路。
3. `TrafficLightTask` 通过 `DeviceGroupContext.start_phone_task()` 启动手机侧 `traffic_light_phone_task`。
4. 手机侧 `TrafficLightPhoneTask` 调用 `TrafficLightProcessor` 产出 `phone.vision.traffic_light.result`。
5. 服务端任务收到有效信号后提交通知，并按策略停止手机任务和视频链路。

当前处理器是业务链路验证用的最小实现，真实 iOS 端检测插件后续应放在本能力目录下的 `phone/ios/`，不能写入 SDK 通用运行时。
