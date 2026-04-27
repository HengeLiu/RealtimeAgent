# SDK 对功能开发支持情况的说明

更新时间：2026-04-28  
评估对象：`openaiglass-sdk` 当前文档、代码与 `openaiglass-for-blind/docs/stage1/plan` 下全部阶段计划  
评估目的：判断 SDK 当前对第一期功能开发计划的支撑程度，并明确功能开发团队遇到哪些问题应继续依赖 SDK，而不是在业务目录中重写系统能力。

## 1. 评估依据

本次评估重点阅读和核对了以下内容：

1. 功能计划：`第一期功能开发计划.md`、`第一期前三项开发落地计划.md`、`第二阶段第4-8项开发落地计划.md`、`第三阶段第9-10项与视觉导航迁移开发落地计划.md`。
2. SDK 架构文档：`openaiglass-sdk/docs/structure-design`、`openaiglass-sdk/docs/sdk-design`、`openaiglass-sdk/docs/stage2` 下现有设计、计划、迭代记录和验收文档。
3. SDK 真实实现：服务端 Python SDK、iOS 手机 SDK 运行时、ESP32 眼镜运行时、`glass-playback`、`phone-mock`、契约测试和集成测试。
4. 业务侧参考实现：仅作为 SDK 使用样板阅读了 `openaiglass-for-blind/capabilities` 和 `openaiglass-for-blind/host`，本评估不要求在业务目录内新增功能代码。

## 2. 总体结论

当前 SDK 已经能支撑功能团队基于 `BaseTool`、`BaseTask`、`BasePhoneTask`、`BasePhoneProcessor` 和 MCP Adapter 继续做大部分第一期能力开发。设备注册绑定、三端配置同步、控制消息、媒体帧、非实时语音主链路、Agent 调用 Tool、SDK 托管 Task、单次抓拍、手机任务、眼镜到手机 JPEG 视频流、设备级回放测试等关键脚手架已经存在，业务侧不应再自行实现这些系统能力。

但 SDK 还不能称为完整产品级三端开发框架。实时语音打断、Skill Runtime 与 agent-core 的完整集成、真实地图服务治理、手机端多视觉模型资源仲裁、公网/NAT 直连、生产级持久化恢复、iOS/ESP32 发布级包化仍然不足。功能开发团队遇到这些问题时，应写入“架构阻塞点说明与改进建议”，不应在 `openaiglass-for-blind` 中绕过 SDK 自建协议、任务中心、连接管理或端侧资源治理。

## 3. 已经能够支持的功能

| 功能 | 支持判断 | SDK 支撑点 | 对功能开发团队的使用口径 |
| --- | --- | --- | --- |
| 1. 眼镜与服务器配对注册 | 已支持 | `ControlRuntime` 支持 `/ws/control`、`device.register`、`device.heartbeat`、pair token、重连覆盖、心跳超时；ESP32 运行时已发送注册和心跳。 | 业务侧只配置设备编号和 token，不直接处理 WebSocket 注册细节。 |
| 2. 眼镜与服务器非实时语音对话 | 已支持主链路 | `VoiceRuntime` 支持 `/ws_audio` 音频段聚合、ASR、Agent/模型调用、下行播放流；语音设计明确第一期半双工、播放期间闭麦。 | 功能开发可以把语音输入视为 Agent Turn，不在业务 Task 中自建语音状态机。 |
| 4. AgentCore 调用服务器 Tool | 已支持 | `AgentFacade`、`ToolRegistry`、`SdkToolAdapter`、`BaseTool` 已支持业务 Tool 注入模型工具面。 | 新短时能力优先实现 `BaseTool.run(context, input_data)` 并返回 `CapabilityResult`。 |
| 5. Tool 与 MCP 最小能力层 | 已支持 | `OpenAIGlassesSDK.register_tool()`、`register_mcp_adapter()`、`DeviceGroupContext.mcp()`、MCP trace 已存在。 | 外部服务能力用 MCP Adapter 暴露，不在 Tool 中硬编码底层调用与 trace。 |
| 6. 单次拍照工具与图片解读 | 已支持基础闭环 | 内置 `capture_photo` Tool 通过 `CameraGateway` 触发眼镜抓拍、落盘图片资产并返回 `MediaAssetRef`；ESP32 支持 `sensor.camera.capture` 与 `sensor.camera.captured`。 | 业务侧可以复用抓拍能力做图片理解，不直接拼控制消息或自行管理图片资产。 |
| 8. 后台任务管理工具和 SDK 托管 Task | 已支持基础闭环 | `BaseTask`、`TaskRuntimeManager` 支持创建、查询、取消、事件派发、超时、事件日志、JSON 快照保存/恢复。 | 长生命周期能力必须做成 `BaseTask`，不要用临时线程或临时协程绕过任务中心。 |
| 9. 手机注册到服务器并与眼镜绑定 | 已支持基础闭环 | `ControlRuntime` 支持 `device_type=phone`、`device.bind`、自动绑定、绑定移除通知；iOS 运行时和 `phone-mock` 支持手机控制连接、心跳和 camera sink。 | 功能侧只声明目标手机和眼镜，不维护绑定表。 |
| 10. 模型通过工具创建眼镜与手机直连视频任务 | 已支持最小闭环 | `start_phone_video_link`、`phone_video_link_task`、`sensor.camera.stream.start/stop`、`MediaFrame(camera_frame)`、iOS `/ws/camera` 接收和 ESP32 JPEG 推流已具备。 | 视频类能力通过 SDK 视频任务启动；业务 Task 只关心任务状态和手机侧处理结果。 |
| 设备级数据回放测试 | 已支持基础能力 | `glass-playback` 可模拟眼镜控制、抓拍和视频流；`phone-mock` 可模拟手机注册、任务接收和事件回传；SDK 契约测试覆盖公共模型。 | 自动化优先走设备级回放，不再使用旧组件级 ScenarioRunner 思路。 |
| 三端配置、日志和预检 | 已支持日常联调 | `openaiglass.config.sync`、`openaiglass.sdk.live-check`、`openaiglass.sdk.preflight` 和结构化日志字段已存在。 | 三端联调前先同步配置和跑 live-check，日志按 `device_id/session_id/task_id/stream_id` 排查。 |

## 4. 存在欠缺但可以继续功能开发的功能

| 功能 | 欠缺点 | 当前可用方式 | SDK 后续应补方向 |
| --- | --- | --- | --- |
| 7. 基于 AMap MCP 的导航能力 | SDK 已有 MCP Adapter 接入面和任务运行时，但真实 AMap 鉴权、配额、超时、错误码、mock/real 切换规范还未产品化。 | 功能侧可以先实现业务侧 AMap Adapter 和 `navigation_task`，所有调用仍走 `context.mcp(...)`。 | SDK 沉淀统一 MCP 配置、错误包装、限流、重试和测试 mock 规范。 |
| 第三阶段 Phase K 手机端视觉能力迁移 | 手机端任务和帧分发已支持，但模型加载、帧率降级、功耗、优先级、资源抢占等端侧治理不足。 | 首批视觉能力可按 `BasePhoneTask + BasePhoneProcessor` 或 iOS `PhoneTaskCapabilityRuntime` 做最小闭环。 | SDK 提供手机端模型资源管理、任务优先级、性能保护和统一检测结果模型。 |
| Phase L 导航执行期任务 | `BaseTask` 能承载导航任务，视频和手机视觉事件也能回流，但路线准备、执行期策略、视觉事件对导航状态的影响仍需要业务样板沉淀。 | 功能侧可以做最小 `navigation_task`：创建、查询、取消、处理若干视觉事件。 | SDK 梳理导航类 Task 模板、通知策略和跨设备事件优先级。 |
| 通知和抢播策略 | SDK 有 `NotificationCoordinator` 和 TaskEvent 桥接，但用户播报、任务通知、视觉告警、Agent 回复之间的完整优先级策略还不够稳定。 | 普通任务完成和低频提示可走 `submit_notification` 或任务事件回流。 | SDK 需要补可配置通知策略、去重、抢占和播放状态观测。 |
| 任务持久化恢复 | `TaskRuntimeManager` 已有 JSON 快照保存和恢复，但不是数据库级事件溯源，也不覆盖多进程、多实例幂等。 | 本地开发和样板能力可用 JSON 快照做恢复验证。 | 生产化前补数据库存储、事件重放、幂等和重启补偿。 |
| Skill 扩展 | `SkillRegistry`、`SkillPolicy`、`SkillSessionState` 有骨架，但当前没有完整 `read_skill` Tool、active skill 工具白名单和 agent-core 集成。 | 复杂能力暂时按“高级 Tool + Task + MCP”实现，Skill 文档可先作为业务说明。 | SDK 继续实现 Skill Runtime：扫描 `SKILL.md`、模型按需读取、工具白名单、审计和测试。 |
| 真实三端发布形态 | Python SDK 可 editable 安装，iOS/ESP32 仍以源码工程形态复用。 | 当前团队内开发可按指南打开业务宿主工程和 ESP-IDF 工程。 | 后续补 iOS SDK 包、ESP-IDF component、版本兼容和外部开发者安装流程。 |

## 5. 当前不能支持的功能

| 功能 | 不能支持的原因 | 功能团队处理方式 |
| --- | --- | --- |
| 3. 实时语音对话并支持用户打断 | 当前语音协议和运行时明确是一期开关：播放期间闭麦，`interrupt_policy=forbid`。通知抢播不等于用户语音打断，也不是电话式全双工。 | 不要在业务能力中私建实时语音链路。需要时记录为 SDK 语音运行时专项。 |
| 全双工语音与播放中持续收音 | 眼镜端、服务端音频聚合、回声处理、VAD、播放跟踪和模型 realtime 接口都未形成统一实现。 | 业务验收按非实时半双工口径，不把“可打断”作为当前能力承诺。 |
| 公网/NAT 场景下稳定眼镜手机直连 | 当前视频链路更接近局域网直连和最小 peer-link 语义，不包含 NAT 穿透、中继、复杂重试、链路质量治理。 | 功能开发只使用 SDK 提供的 `start_phone_video_link`，不要自行做网络穿透方案。 |
| 生产级多手机、多眼镜组织管理 | 当前绑定支持一对一设备组和冲突校验，但没有账号体系、远程配置中心、权限隔离和大规模设备目录。 | 阶段一功能只按固定设备组开发；账号和设备资产管理另列 SDK/平台需求。 |
| 完整旧项目视觉工作流一次性迁移 | SDK 有承载面，但旧项目中的 YOLO、盲道、红绿灯、避障、过街、导航策略不能由 SDK 一次性替业务实现。 | 功能团队按一个能力一个 Task/Tool/手机插件迁移；SDK 只负责通用运行时和阻塞点优化。 |

## 6. 对功能开发团队的边界建议

1. 新短时能力写 `BaseTool`，新后台能力写 `BaseTask`，手机侧持续处理写 `BasePhoneTask` 或 iOS `PhoneTaskCapabilityRuntime`，外部服务写 MCP Adapter。
2. 业务代码不得维护设备连接表、绑定表、WebSocket 路由、控制消息编解码、视频链路生命周期和任务状态存储。
3. Tool/Task 获取硬件能力、手机能力、任务能力和 MCP 能力时，只通过 SDK 提供的 `context` 或 `TaskContext.device_group`。
4. 每个新能力至少补成功路径、缺设备或失败路径、取消或事件推进路径的设备级测试；真实 iPhone 未准备好时先用 `phone-mock`。
5. 遇到实时语音、Skill 白名单、端侧模型资源治理、公网 peer-link、生产持久化、发布包化等问题，写入阻塞点文档，由 SDK 团队迭代。

## 7. SDK 团队建议优先级

1. 优先补 Skill Runtime 与 agent-core 集成，因为这直接影响后续“像搭积木一样扩展复杂能力”的开发体验。
2. 其次补手机视觉运行时治理，包括多任务共享帧、模型资源生命周期、帧率控制和性能保护。
3. 然后补真实 AMap/MCP 服务治理和导航类 Task 模板，让路线准备到执行期任务有稳定样板。
4. 再补任务持久化的生产化方案，包括数据库存储、事件重放和多实例幂等。
5. 实时语音打断和全双工语音应作为独立大迭代处理，不建议夹在普通业务能力迭代中零散修改。
