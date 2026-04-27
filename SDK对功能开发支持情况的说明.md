# SDK 对功能开发支持情况的说明

更新时间：2026-04-27  
评估对象：`openaiglass-sdk` 当前代码与 `openaiglass-for-blind` 当前业务实现  
评估目的：判断项目完全交给功能开发团队后，SDK 能否支撑后续真实场景能力开发，以及哪些问题仍应回写到 SDK 改进文档。

## 1. 总体结论

当前 SDK 已经具备“功能开发团队可以独立继续迭代”的基础。服务端 Python SDK 已提供统一装配入口、设备注册与绑定、控制消息、Agent Tool 注入、MCP Adapter、SDK 托管 Task、手机任务、设备组上下文、三端配置同步、预检、契约测试、`glass-playback` 和 `phone-mock` 等能力。业务侧已经用这些公开能力实现了 `find_object`、`traffic_light`、`navigation` 和 `timer` 四类样板能力，没有必要再在业务目录里重写 WebSocket、绑定表、任务存储、视频控制协议或 Agent 工具桥接。

但 SDK 还不是完整产品级三端框架。实时语音打断、全双工语音、公网/NAT 穿透、真实地图服务治理、手机端模型资源仲裁、生产级持久化恢复、iOS/ESP32 发布级包化仍未完成。功能开发团队遇到这些问题时，应记录到 [架构阻塞点说明与改进建议.md](openaiglass-for-blind/docs/stage1/develop/架构阻塞点说明与改进建议.md)，不要在 `openaiglass-for-blind` 里补 SDK 层系统能力。

## 2. SDK 已经能稳定支撑的部分

| 领域 | 支持情况 | 当前代码依据 |
| --- | --- | --- |
| 目录和团队边界 | 已支持 | `openaiglass-sdk` 与 `openaiglass-for-blind` 已拆分；`openaiglass.sdk.preflight` 有边界检查，防止 SDK 反向依赖业务代码。 |
| 服务端 SDK 装配 | 已支持 | `OpenAIGlassesSDK` 提供 `register_tool`、`register_task`、`register_phone_processor`、`register_phone_task`、`register_sensor_provider`、`register_mcp_adapter`、`build_server_handle`。 |
| 三端统一模型 | 已支持基础模型 | `CapabilityResult`、`CapabilityError`、`DeviceEndpoint`、`DeviceGroup`、`TaskRuntimeSnapshot`、`SensorReading` 有契约测试和金样。 |
| 设备注册、心跳、绑定 | 已支持 | 控制运行时支持 `device.register`、`device.heartbeat`、pair token、重连替换、手机与眼镜自动绑定、离线标记和运行态查询。 |
| 设备组上下文 | 已支持 | `DeviceGroupContext` 提供 `require_glass`、`require_phone`、`query_devices`、`capture_photo`、`start_phone_video_link`、`start_phone_task`、`submit_notification`、`create_task`、`mcp`。 |
| Agent 调用业务 Tool | 已支持 | `SdkToolAdapter` 将业务 `BaseTool` 注入 agent-core，业务 Tool 只需要返回 `CapabilityResult`。 |
| 业务 Task | 已支持最小运行时 | `TaskRuntimeManager` 支持创建、查询、取消、事件派发、事件日志、超时、快照导出/恢复、JSON 保存/加载。 |
| 手机侧任务和处理器 | 已支持 | `BasePhoneTask`、`BasePhoneProcessor`、`PhoneRuntime` 支持任务启动、停止、单帧处理、按 `stream_id` 和 `task_types` 多任务分发。 |
| MCP 调用 | 已支持 | 业务侧可注册 `BaseMcpAdapter`，Tool/Task 通过 `context.mcp(...)` 调用，SDK 记录 MCP trace。 |
| 视频链路最小任务 | 已支持基础语义 | `phone_video_link_task` 由 SDK 系统任务托管，支持创建、查询、取消、超时、peer-link 与 camera stream 事件推进。 |
| 三端配置与启动 | 已支持日常联调 | `openaiglass.config.sync` 同步服务端、iOS、phone-mock、ESP32 配置；`openaiglass.server.*`、`openaiglass.phone.*`、`openaiglass.glass.*` 提供统一命令。 |
| 设备级回放测试 | 已支持基础闭环 | `glass-playback` 可模拟眼镜控制、音频、抓拍和视频流；`phone-mock` 可按真实 phone 协议注册、接收任务并回报 mock 事件。 |
| SDK 预检和回归 | 已支持 | `openaiglass.sdk.preflight` 覆盖入口、编译、边界、包、契约、pytest、健康检查；测试目录覆盖注册、语音、Agent、Task、WebSocket、日志和 phone-mock。 |

## 3. 业务侧已经验证的 SDK 使用方式

| 业务能力 | 当前实现 | 对 SDK 支持能力的验证 |
| --- | --- | --- |
| `find_object` | `StartFindObjectTool` 创建 `find_object_task`，Task 启动手机视频链路和 `find_object_phone_task`，手机结果事件推进任务完成。 | 验证了 Tool、Task、手机任务、视频链路、任务事件、通知和调试启动接口。 |
| `traffic_light` | 服务端 Task 启动手机视觉任务，手机侧处理红绿灯帧并上报结果。 | 验证了同类视觉任务可复用 SDK 手机任务模型。 |
| `navigation` | `PrepareNavigationTool` 通过 `context.mcp(...)` 调用业务侧 mock AMap adapter，创建 `navigation_task` 并处理进度/视觉事件。 | 验证了 MCP Adapter、任务状态推进、业务事件和导航类长任务样板。 |
| `timer` | `StartTimerTool` 创建 `timer_task`，通过事件推进运行、完成、取消。 | 验证了 SDK 托管 Task、事件日志、超时和无业务线程的后台任务样板。 |
| iOS 手机宿主 | 业务工程只保留薄 Xcode 入口和配置，复用 SDK iOS 运行时。 | 验证了手机端注册、心跳、视频接收、任务插件和结果上报可以作为通用宿主使用。 |
| `phone-mock` | 业务目录只放 JSON 配置，运行时代码在 SDK。 | 验证了功能开发可以不用真实 iPhone 先做设备级自动化闭环。 |

## 4. 仍然不能由功能团队自行解决的 SDK 缺口

| 缺口 | 当前影响 | 处理建议 |
| --- | --- | --- |
| 实时语音对话和用户打断 | 当前主链路可做非实时语音问答；播放期持续收音、打断、半双工/全双工状态机还不完整。 | 功能团队不要在业务 Task 里私建实时语音协议；由 SDK 团队补 voice runtime、端侧状态机和回归测试。 |
| 普通回复端到端真流式 | 普通文本 delta 到 TTS/播放的完整链路仍需继续核对和收敛，长回复首音频延迟可能不稳定。 | 作为 SDK 语音链路优化项跟踪，业务侧只按当前非实时体验验收。 |
| 生产级任务持久化恢复 | SDK 已有 JSON 快照保存/恢复，但不是数据库级事件溯源，也没有多实例幂等补偿。 | 长时间生产任务先保持最小策略；需要重启恢复时由 SDK 提供数据库/事件日志方案。 |
| 真实公网 peer-link 治理 | 当前是局域网/直连最小任务语义，不包含 NAT 穿透、公网中继、自动重试和链路补偿。 | 功能侧只使用 `start_phone_video_link`，不要自行扩展网络治理。 |
| 手机端多视觉模型资源管理 | SDK 可多任务分发帧，但没有模型加载、帧率降级、优先级、功耗和端侧性能保护框架。 | 新视觉能力先做单模型或 mock 闭环；资源仲裁写入阻塞点。 |
| 真实 AMap 服务治理 | 业务侧当前是 mock adapter，真实鉴权、配额、限流、超时、错误码和 mock/real 切换规范未固化。 | 可以先由业务实现真实 adapter，但通用错误规范和配置开关应沉淀回 SDK。 |
| iOS/ESP32 发布级包化 | iOS 仍以源码/Xcode 工程复用，ESP32 仍是 ESP-IDF 工程，不是稳定发布包或 component。 | 功能开发按当前宿主工程联调；正式外部开发者交付前需 SDK 团队包化。 |

## 5. 对功能开发团队的下一步建议

1. 后续业务代码只改 `openaiglass-for-blind/capabilities`、`openaiglass-for-blind/host` 和业务文档；除非明确承担 SDK 任务，否则不要改 `openaiglass-sdk`。
2. 新能力优先复制现有四个样板之一：短时能力用 `BaseTool`，后台能力用 `BaseTask`，手机视觉/传感器能力用 `BasePhoneTask` 和 `BasePhoneProcessor`，外部服务能力用 MCP Adapter。
3. 业务 Task 中需要硬件、手机、眼镜、通知、MCP、子任务时，只通过 `TaskContext.device_group` 调 SDK 公开方法，不直接拼控制消息。
4. 每个新能力至少补一条成功、一条失败或缺设备、一条取消/事件推进的设备级测试；真实手机未准备好时优先用 `phone-mock` 和 `glass-playback`。
5. 遇到实时语音、网络穿透、任务持久化、模型资源并发、端侧包化等系统问题，直接写入阻塞点文档，保持功能目录干净。

## 6. 建议优先阅读的文档

| 文档 | 用途 |
| --- | --- |
| [SDK安装与能力开发指南.md](openaiglass-for-blind/SDK安装与能力开发指南.md) | 功能开发者日常安装、配置、启动、开发和测试入口。 |
| [架构阻塞点说明与改进建议.md](openaiglass-for-blind/docs/stage1/develop/架构阻塞点说明与改进建议.md) | 记录功能开发中发现但不应由业务侧解决的 SDK 问题。 |
| [当前实现状态.md](openaiglass-for-blind/docs/当前实现状态.md) | 快速了解当前真实运行面和已实现业务能力。 |
| [SDK真机联调前检查与联调步骤.md](openaiglass-sdk/docs/sdk-design/SDK真机联调前检查与联调步骤.md) | 进入真实眼镜、手机、服务器三端联调前使用。 |
| [SDK v5 迭代记录](openaiglass-sdk/docs/stage2/iteration-v4.md) | 了解当前 SDK 最近补齐的 Task、快照和手机多任务分发能力。 |
