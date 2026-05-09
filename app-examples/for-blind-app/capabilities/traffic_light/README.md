# traffic_light 迁移样板

能力价值：识别红绿灯状态，并在安全时机提醒用户。

audio-chat 迁移路径：

1. Task 使用 `context.devices.sensors.rgb.one()` 请求一张 RGB 图片资产。
2. YOLO 迁移完成前，Task 只生成 mock 红绿灯状态。
3. 识别结果通过 `TaskSignal` 回流。
4. 用户提示通过 Output Service 播报，不直接控制播放器。

参考：

- `app-examples/for-blind-app/capabilities/traffic_light/task.py`

验收要求：

- 至少有一个红灯、绿灯或未知状态的回放样例。
- `output-decisions.jsonl` 能解释提示是否播报、排队或被仲裁丢弃。
