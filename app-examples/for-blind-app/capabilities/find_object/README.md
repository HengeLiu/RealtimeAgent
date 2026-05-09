# find_object 迁移样板

能力价值：用户询问“帮我找一下手机在哪里”或“看一下前方有什么”时，系统请求视觉资产并返回识别结果。

audio-chat 迁移路径：

1. Tool 使用 `await context.devices.sensors.rgb.one(...)` 请求单帧图片。
2. 端侧通过 `sensor.rgb` stream 上传 JPEG / PNG。
3. Tool 返回 `AssetRef` 和模型可读摘要。
4. 需要持续引导时升级为 Task，使用 `context.devices.sensors.rgb.stream()` 消费多帧。

参考：

- `app-examples/for-blind-app/capabilities/capture_photo/tool.py`
- `app-examples/for-blind-app/templates/find_object/tool.py`

验收要求：

- 回放里能看到 `sensor.rgb` 资产。
- Tool 不直接 import 内部 service。
- 控制事件 payload 不携带图片 base64。
