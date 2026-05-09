# audio-chat 能力梳理清单

本文用于审查当前 SDK 与 `for-blind-app` 中已经实现或可注册的 Tool / Task。清单依据当前代码整理，并结合 [Context 与设备 API 设计说明](context-device-api-design.md) 中的新定位判断每项能力应归属 Tool、Task 还是基础设施。

## 1. 判断口径

| 维度 | 判断规则 |
| --- | --- |
| 新定位归属 | Tool 是 Agent Loop 内的一次短生命周期调用；Task 使用 DeviceContext，负责持续数据流、异步命令、跨设备状态、取消、超时和恢复。 |
| 盲人相关 | 强相关表示该能力直接服务视觉补偿、出行安全、导航、红绿灯、找物或眼前画面理解；否表示通用 SDK、调试、记忆、搜索、计时或样板能力。 |
| 当前注册状态 | `for-blind-app` 当前会注册 SDK 内置 Tool、启用的扩展 Tool，以及 `capabilities/` 下自动发现的业务 Tool / Task。`templates/` 目录不纳入当前注册能力。 |

## 2. Tool 清单

| # | 名称 | 来源 | 当前注册状态 | 当前做什么 | 当前实现逻辑 | 新定位归属 | 盲人相关 |
| ---: | --- | --- | --- | --- | --- | --- | --- |
| 1 | `request_asset` | SDK | 已注册 | 请求传感器资产 | 调用 `context.devices.sensors.rgb.one()`，返回 AssetRef。 | Tool | 否 |
| 2 | `capture_photo` | SDK | 已注册 | 获取当前画面 | 请求一次 `sensor.rgb` 图片，超时返回失败。 | Tool | 强相关 |
| 3 | `publish_device_command` | SDK | 已注册 | 发轻量设备命令 | 发布 `command.requested`。 | Tool | 否 |
| 4 | `start_phone_video_link` | SDK | 已注册 | 建立手机或摄像头画面链路 | 发布 `stream.control.open.requested` 请求连续 RGB，返回 link 投递结果。 | 边界待收敛：启动 Tool + 持续 Task | 强相关 |
| 5 | `close_continuous_dialog` | SDK | 已注册 | 结束连续对话 | 调用 `close_continuous_dialog()` 安排当前会话关闭。 | Tool | 否 |
| 6 | `query_device_state` | SDK | 已注册 | 查询设备状态 | 读取当前用户 active devices 快照，可选择是否包含订阅摘要。 | Tool | 否 |
| 7 | `query_task_status` | SDK | 已注册 | 查询任务状态 | 通过 TaskEngine 查询 TaskRef。 | Tool，Task 查询专用工具 | 否 |
| 8 | `cancel_task` | SDK | 已注册 | 取消任务 | 调用 TaskEngine.cancel，并返回更新后的 TaskRef。 | Tool，Task 取消专用工具 | 否 |
| 9 | `memory_search` | SDK | 已注册 | 查询长期记忆 | 按 topic 调 MemoryService 读取记忆详情。 | Tool，Memory 专用工具 | 否 |
| 10 | `manage_memory` | SDK | 已注册 | 写入、更新或删除长期记忆 | 调 MemoryService.manage 处理本轮需要维护的长期记忆。 | Tool，Memory 专用工具 | 否 |
| 11 | `read_skill` | SDK | 当前未注册，`skill.enabled=false` | 读取 Skill 文档 | 调 SkillService.read_skill 返回受控 Skill 内容。 | Tool，Skill 专用工具 | 否 |
| 12 | `mcp_call` | SDK | 已注册 | 通用 MCP 调用 | 透传调用配置中的 MCP tool。 | Tool，MCP wrapper | 否 |
| 13 | `find_object_capture` | `for-blind-app` | 已注册 | 单次找物抓拍 | 请求一次 RGB，返回 mock 找物结果。 | Tool | 强相关 |
| 14 | `start_find_object` | `for-blind-app` | 已注册 | 启动持续找物 | 创建 `find_object_vision_task`。 | Tool，启动 Task 专用工具 | 强相关 |
| 15 | `prepare_navigation` | `for-blind-app` | 已注册 | 路线准备 | 调 MCP `amap.route_plan` mock，失败时返回 fallback。 | Tool，MCP wrapper | 强相关 |
| 16 | `start_navigation` | `for-blind-app` | 已注册 | 启动导航 | 创建 `navigation_task`。 | Tool，启动 Task 专用工具 | 强相关 |
| 17 | `echo_text` | `for-blind-app` | 已注册 | 联调 echo | 返回输入文本和当前用户在线设备数。 | Tool | 否 |
| 18 | `search_web` | `for-blind-app` | 已注册 | 搜索资料 | 调 MCP `web.search` mock，失败时返回 fallback。 | Tool，MCP wrapper | 否 |
| 19 | `timer` | `for-blind-app` | 已注册 | 计时器入口 | `create/start` 创建 `timer_task`，`query` 查询，`cancel` 取消。 | Tool，Task 管理入口 | 否 |
| 20 | `start_traffic_light` | `for-blind-app` | 已注册 | 启动红绿灯识别 | 创建 `traffic_light_task`。 | Tool，启动 Task 专用工具 | 强相关 |

## 3. 业务 Task 清单

| # | 名称 | 来源 | 当前注册状态 | 当前做什么 | 当前实现逻辑 | 新定位归属 | 盲人相关 |
| ---: | --- | --- | --- | --- | --- | --- | --- |
| 21 | `continuous_rgb_analyze` | `for-blind-app` | 已注册 | 连续 RGB 分析样板 | 请求连续 RGB，按 correlation_id 收集 N 帧并输出结果。 | Task | 否 |
| 22 | `find_object_phone_task` | `for-blind-app` | 已注册 | 端侧找物任务 | 发 `phone.task.start`，要求端侧通过 `sensor.rgb` stream 上传视觉帧。 | Task | 强相关 |
| 23 | `find_object_vision_task` | `for-blind-app` | 已注册 | 持续找物 | 配置连续 RGB stream，收集多帧，发找物事件并完成。 | Task | 强相关 |
| 24 | `navigation_task` | `for-blind-app` | 已注册 | 导航执行期状态推进 | 生成偏航、接近终点、视觉确认等事件；视觉确认时请求一次 RGB。 | Task | 强相关 |
| 25 | `sample_reminder` | `for-blind-app` | 已注册 | Task 样板 | 只发 `task.sample.started`，不操作设备。 | Task | 否 |
| 26 | `timer` | `for-blind-app` | 已注册 | 最小计时器样板 | 发启动提示和 `timer.started`，不真实等待。 | Task | 否 |
| 27 | `timer_task` | `for-blind-app` | 已注册 | 正式计时器 | 调度 `timer.due`，到点后完成；取消时通知。 | Task | 否 |
| 28 | `traffic_light_phone_task` | `for-blind-app` | 已注册 | 端侧红绿灯任务 | 发 `phone.task.start`，由端侧执行红绿灯识别并通过 stream 上传 RGB。 | Task | 强相关 |
| 29 | `traffic_light_task` | `for-blind-app` | 已注册 | 红绿灯识别 | 配置连续 RGB stream，生成信号灯状态和通行建议并播报。 | Task | 强相关 |

## 4. SDK Task 运行时清单

`audio-chat-sdk/audio_chat/tasks.py` 当前没有内置业务 Task 子类，主要提供 Task 基础设施。以下项目不是模型可直接调用的 Tool，也不是业务 Task，但应纳入能力规整范围。

| # | 名称 | 来源 | 当前做什么 | 当前实现逻辑 | 新定位归属 | 盲人相关 |
| ---: | --- | --- | --- | --- | --- | --- |
| 30 | `TaskSpec` | SDK | 描述 Task 规格 | 收敛 task_type、version、timeout、cancel_supported、max_running_per_user。 | Task 基础设施 | 否 |
| 31 | `TaskRef` | SDK | 任务引用 | 对外暴露 task_id、task_type、state、summary、metadata。 | Task 基础设施 | 否 |
| 32 | `TaskEvent` | SDK | 任务事件 | 承载状态回流、通知、Agent 决策字段和 artifacts。 | Task 基础设施 | 否 |
| 33 | `DeviceContext` / `TaskContext` | SDK | Task 执行上下文 | 注入 devices、output、assets、bridge、engine，并提供 complete、fail、schedule_event。 | Task 基础设施 | 否 |
| 34 | `BaseTask` | SDK | 业务 Task 基类 | 定义 `on_start()`、`on_event()`、`on_cancel()` 扩展点。 | Task 基础设施 | 否 |
| 35 | `TaskStateMachine` | SDK | 状态机 | 校验 scheduled、running、waiting_external、completed、cancelled、failed、timeout 等状态流转。 | Task 基础设施 | 否 |
| 36 | `TaskStore` | SDK | 内存任务存储 | 保存 TaskRef 和 TaskEvent。 | Task 基础设施 | 否 |
| 37 | `JsonlTaskStore` | SDK | JSONL 持久化 | 追加写入任务快照和事件，启动时重放恢复。 | Task 基础设施 | 否 |
| 38 | `TaskRegistry` | SDK | Task 注册表 | 按 `task_type` 注册、查找和列出 Task 类。 | Task 基础设施 | 否 |
| 39 | `TaskAutoDiscovery` | SDK | 自动发现 Task | 递归导入包，发现 BaseTask 子类并检查重复 task_type。 | Task 基础设施 | 否 |
| 40 | `TaskEventBridge` | SDK | 事件桥接 | 记录任务事件，并按事件字段转通知或 Agent 可读轮次。 | Task 基础设施 | 否 |
| 41 | `TaskExecutor` | SDK | Task 执行器 | 调用 Task 的 start、event、cancel 回调。 | Task 基础设施 | 否 |
| 42 | `TaskScheduler` | SDK | 调度和超时判断 | 计算 deadline，判断恢复和过期。 | Task 基础设施 | 否 |
| 43 | `TaskEngine` | SDK | Task 引擎 | 创建、恢复、查询、处理事件、取消、完成、失败任务，并注入 TaskContext。 | Task 基础设施 | 否 |

## 5. 当前发现的问题和待审查点

1. `start_phone_video_link` 当前是 Tool，但会打开连续 RGB stream。按新定位，它更适合拆成“启动 Tool + 持续 Task”，或者明确只作为兼容入口保留。
2. `read_skill` 是 SDK 扩展 Tool，但 `for-blind-app/server.yaml` 当前关闭 Skill，因此没有注册到当前 app。
3. 目前源码中没有 `ConfigureAssetStreamTool` / `configure_asset_stream` 类；如果后续审查记录里出现该名称，应按当前代码视为不存在或已被其它设备 API 替代。
4. `basic_timer.task.TimerTask` 的 task_type 是 `timer`，而 `timer.task.TimerTask` 的 task_type 是 `timer_task`。前者是最小样板，后者才是当前计时器 Tool 实际创建的正式任务。
5. `sample_tool`、`sample_task`、`continuous_rgb_analyze` 更像 SDK 验证样板，不是盲人业务核心能力。
