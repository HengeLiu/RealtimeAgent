# realtime-agent 能力规整清单

本文记录本轮 Tool / Task 规整后的目标状态。判断依据见 [Context 与设备 API 设计说明](../../../../audio-server/docs/reference/context-api.md)：Tool 是 Agent Loop 内一次短调用；Task 是后台运行实例，由自动生成的专用启动 Tool 创建，再由统一运行时管理器查询和取消。

## SDK 内置 Tool

| # | 名称 | 当前状态 | 作用 | 是否 SDK Builtin |
| ---: | --- | --- | --- | --- |
| 1 | `close_continuous_dialog` | 保留 | 结束连续对话或安排会话关闭。 | 是 |
| 2 | `query_device_state` | 保留 | 查询当前用户在线设备和能力快照。 | 是 |
| 3 | `task_runtime_manager` | 保留 | 查询、取消和列出 Task 运行实例。 | 是 |
| 4 | `memory_search` | 保留 | 查询长期记忆。 | 是，受 memory 配置控制 |
| 5 | `manage_memory` | 保留 | 写入、更新或删除长期记忆。 | 是，受 memory 配置控制 |
| 6 | `read_skill` | 保留 | 读取受控 Skill 文档。 | 是，受 skill 配置控制 |
| 7 | `mcp_call` | 保留 | 调用已配置 MCP tool。 | 是，受 mcp 配置控制 |

以下旧工具不再作为 SDK 内置实现：`request_asset`、`capture_photo`、`publish_device_command`、`query_task_status`、`cancel_task`。对应底层能力分别保留在 Context 设备 API、自动生成的 `start_*_task` Tool 或 `task_runtime_manager` 中。

## for-blind-app Tool

| # | 名称 | 当前状态 | 作用 | 是否 SDK Builtin |
| ---: | --- | --- | --- | --- |
| 8 | `capture_photo` | App 能力，当前通过 `tools.denylist` 暂不暴露给模型 | 通过 `context.devices.sensors.rgb.one()` 抓拍当前画面，返回 AssetRef。 | 否 |
| 9 | `interpret_image` | App 能力，当前通过 `tools.denylist` 暂不暴露给模型 | 解读指定图片资产。 | 否 |
| 10 | `interpret_current_view` | App 能力，当前通过 `tools.denylist` 暂不暴露给模型 | 抓取当前画面并做图片解读编排。 | 否 |
| 11 | `query_route_plan` | App 能力 | 调 Amap MCP `amap.route_plan` 查询路线；配置层可用 `target_name` 映射到远端真实工具名。未配置时返回明确 fallback。 | 否 |
| 12 | `search_web` | App 能力 | 调 Bocha Web Search API 搜索资料；未配置 API Key 时返回明确 fallback。 | 否 |

业务 Task 不再手写重复的启动 Tool；SDK 会按 TaskSpec 自动生成 `start_find_object_task`、`start_traffic_light_task`、`start_timer_task` 这类模型可见启动 Tool。

## for-blind-app Task

| # | 名称 | 当前状态 | 作用 | 盲人相关 |
| ---: | --- | --- | --- | --- |
| 13 | `find_object_task` | 保留，peer video 版 | 通过 `peer.video.receiver.start` / `peer.video.sender.start` 编排 phone 与 glass 建立视频任务链路，phone 端运行 YOLOE 找物并回报结果。 | 强相关 |
| 14 | `traffic_light_task` | 保留，peer video 版 | 通过 peer video 编排 phone 与 glass 建立视频任务链路，phone 端运行红绿灯 YOLO + HSV fallback，稳定绿灯或超时后回报结果。 | 强相关 |
| 15 | `timer_task` | 保留 | 调度 `timer.due`，到点后完成；取消时播报。 | 否 |

连续视觉样板、端侧 phone task 迁移样板、导航执行期样板、提醒样板和重复计时器样板已清理。
端侧视觉任务已经收敛到 peer video 编排；旧 `phone.task.start` 仅作为协议测试和迁移参考。

## 规划中的照片资产链路重构

为支持 360 识别、万物监测、定时视觉任务和更长周期的自定义视觉能力，后续需要统一
`sensor.rgb` 照片资产上传、turn 级 buffer、业务消费 claim、Omni / Vision 模型图片
append 适配和协议测试。设计和开发计划见：

- [照片资产处理链路重构设计](../photo-asset-pipeline-design.md)
- [照片资产处理链路重构开发计划](../photo-asset-pipeline-implementation-plan.md)

## SDK Task 基础设施

| # | 名称 | 作用 |
| ---: | --- | --- |
| 14 | `TaskSpec` | 描述 Task 类型、版本、超时、取消能力和用户级并发限制。 |
| 15 | `TaskRef` | 对外暴露任务 ID、类型、状态、摘要和 metadata。 |
| 16 | `TaskSignal` | 承载 Task 状态回流、通知、Agent 决策字段和 artifacts。 |
| 17 | `TaskContext` | 注入 devices、output、assets、bridge、engine，并提供 complete、fail、schedule_signal。 |
| 18 | `BaseTask` | 定义 `on_start()`、`on_signal()`、`on_cancel()` 扩展点。 |
| 19 | `TaskRegistry` | 注册、查找和列出 Task 类型。 |
| 20 | `TaskEngine` | 创建、恢复、查询、处理信号、取消、完成、失败任务，并注入 TaskContext。 |
| 21 | `TaskSignalBridge` | 记录任务信号，并按信号字段转通知或 Agent 可读轮次。 |
