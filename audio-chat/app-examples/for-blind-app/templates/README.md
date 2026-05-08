# for-blind-app 能力模板

本目录保存业务能力迁移时可以参考的 Tool / Task 写法。模板属于
`for-blind-app` 的附属开发资料，不会被 `capabilities` 自动发现，也不会在
server 启动时直接暴露给模型。

| 模板 | 适用场景 | 关键边界 |
| --- | --- | --- |
| `find_object/tool.py` | 一次性视觉 Tool，例如请求当前画面后完成一次找物分析。 | 图片字节通过 `sensor.rgb` stream 进入 Asset Service，Tool 只拿资产引用。 |
| `continuous_rgb_analyze/task.py` | 持续视觉 Task，例如找物、红绿灯或导航执行期视觉分析。 | 通过控制事件请求端侧持续上传，再用 `watch_assets()` 消费资产。 |
| `notification_task/task.py` | 后台任务通知，例如计时到点、状态变化或异常提醒。 | 只提交结构化输出，不直接操作播放器或 WebSocket。 |

开发约束：

1. 不硬编码 `device_id` 做点对点发送。
2. 不新增隐藏 RPC，不直接操作 WebSocket。
3. 大字节媒体只走 `sensor.*` 或 `actuator.*` stream。
4. 控制事件 payload 只放语义、配置和关联 ID。
5. Tool / Task 需要设备能力时，只通过 `context.devices` 使用公开 API。
