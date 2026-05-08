# traffic_light 迁移样板

旧能力价值：识别红绿灯状态，并在安全时机提醒用户。

audio-chat 迁移路径：

1. Task 发布 `stream.control.configure.requested`，请求 `sensor.rgb` 连续上传。
2. phone mock 或 iOS 端侧声明视觉能力和 stream subscription。
3. Task 用 `watch_assets("sensor.rgb", correlation_id=...)` 消费帧。
4. 识别结果通过 `TaskEvent` 回流。
5. 用户提示通过 Output Service 播报，不直接控制播放器。

参考：

- `app-examples/for-blind-app/capabilities/continuous_rgb_analyze/task.py`
- `app-examples/for-blind-app/templates/continuous_rgb_analyze/task.py`

验收要求：

- 至少有一个红灯、绿灯或未知状态的回放样例。
- `output-decisions.jsonl` 能解释提示是否播报、排队或被仲裁丢弃。
