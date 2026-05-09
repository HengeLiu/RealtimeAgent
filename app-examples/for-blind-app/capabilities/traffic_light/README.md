# traffic_light 迁移样板

能力价值：识别红绿灯状态，并在安全时机提醒用户。

audio-chat 迁移路径：

1. Task 使用 `context.devices.sensors.rgb.stream()`，由 SDK 请求 `sensor.rgb` 连续上传。
2. phone mock 或 iOS 端侧声明视觉能力和 stream route。
3. Task 直接从 typed stream 消费 `AssetRef` 帧。
4. 识别结果通过 `TaskSignal` 回流。
5. 用户提示通过 Output Service 播报，不直接控制播放器。

参考：

- `app-examples/for-blind-app/capabilities/continuous_rgb_analyze/task.py`
- `app-examples/for-blind-app/templates/continuous_rgb_analyze/task.py`

验收要求：

- 至少有一个红灯、绿灯或未知状态的回放样例。
- `output-decisions.jsonl` 能解释提示是否播报、排队或被仲裁丢弃。
