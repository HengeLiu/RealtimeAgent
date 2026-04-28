# SDK 对功能开发支持情况的说明

更新时间：2026-04-28
评估版本：sdk-v10
评估对象：`openaiglass-sdk` 对 `openaiglass-for-blind/docs/stage1/plan` 中第一期能力开发的支撑情况。

## 评估边界

本文只评估“为了支持功能开发，SDK 需要具备的三端开发框架能力”，不评估 `openaiglass-for-blind` 中任何业务功能代码是否已经实现。

本轮评估没有新增或修改业务功能代码。功能开发团队仍应按照 `openaiglass-for-blind/SDK安装与能力开发指南.md` 使用 SDK 暴露的能力；遇到 SDK 能力不足时，应把阻塞点和改进建议写入文档，避免在业务层重写 SDK 负责的系统问题。

## 评估依据

本次评估阅读了以下材料：

- `openaiglass-for-blind/docs/stage1/plan/第一期功能开发计划.md`
- `openaiglass-for-blind/docs/stage1/plan/第一期前三项开发落地计划.md`
- `openaiglass-for-blind/docs/stage1/plan/第二阶段第4-8项开发落地计划.md`
- `openaiglass-for-blind/docs/stage1/plan/第三阶段第9-10项与视觉导航迁移开发落地计划.md`
- `openaiglass-for-blind/SDK安装与能力开发指南.md`
- `openaiglass-sdk/docs` 下的 SDK 设计、结构设计、stage2 计划和迭代文档
- `openaiglass-sdk/server-python`、`openaiglass-sdk/phone-ios`、`openaiglass-sdk/glass-esp32`、`openaiglass-sdk/glass-playback`、`openaiglass-sdk/phone-mock` 中与 SDK 能力相关的实现

## 评估口径

- 已经能够支持：SDK 已经提供公开接口、运行时或测试工具，功能开发团队可以直接基于这些能力开发，不需要自行处理底层协议、绑定、任务状态或设备消息编解码。
- 存在欠缺：SDK 已经有入口或最小闭环，但在生产稳定性、通用抽象、资源管理、测试支持或真实设备适配上还不完整。功能开发团队可以在明确边界内继续开发，也可以记录阻塞点。
- 不能支持：当前 SDK 没有可靠承载路径，功能开发团队不应在业务层自行补齐，否则会把 SDK 应该解决的系统问题扩散到业务代码。

## 总体结论

sdk-v10 已经可以支撑第一期中“非实时语音交互、普通文本流式 TTS 透传、通知仲裁与高优先级通知抢播、AgentCore 工具调用、Tool/MCP 接入、最小 Skill Runtime、抓拍图片理解、SDK 托管 Task、眼镜与手机注册绑定、账号级设备组织、手机视频链路任务最小闭环、手机视觉资源策略最小闭环、设备级回放测试”等主要基础能力。

当前 SDK 的主要欠缺集中在：实时语音用户打断、完整播放仲裁、真实地图服务适配治理、真 iOS 手机视觉资源池、导航任务通用模板、任务持久化生产化、三端 SDK 打包形态、以及更强的自动化回放断言能力。

因此，功能开发团队目前可以围绕 `BaseTool`、`BaseTask`、`BasePhoneTask`、`BasePhoneProcessor`、`DeviceGroupContext`、`context.mcp` 和回放测试工具继续开发；但不应自行改写 WebSocket 协议、设备绑定、任务生命周期、视频链路控制、全局上下文维护等 SDK 层职责。

## 已经能够支持的能力

| SDK 能力 | 支撑的计划目标 | 当前支持情况 | 功能开发使用边界 |
| --- | --- | --- | --- |
| 设备注册、心跳、重连和运行态诊断 | 第 1 项眼镜服务端注册；第 9 项手机注册；Phase A/B/I | `/ws/control` 已支持 glass、phone 注册，包含设备标识、能力声明、心跳、重连诊断和运行态快照。 | 功能代码不应自行实现注册协议，只需要依赖 SDK 暴露的在线设备和设备组上下文。 |
| 眼镜、手机、服务器设备组绑定和账号级组织 | 第 1 项和第 9 项绑定关系；Phase B/I | `DeviceGroupRuntime`、`ControlRuntime` 已支持一对一 glass-phone 绑定、自动绑定、指定目标绑定、`account_id/user_id` 注册透传、账号级设备索引和跨账号绑定拒绝。 | 业务只表达“哪个账号下哪个眼镜与哪个手机绑定”的产品关系，不处理控制通道细节和全局设备表。 |
| 三端统一控制消息与媒体帧模型 | Phase A/C/J/K | SDK 已提供 `ControlMessage`、`MediaFrame`、音频帧、相机帧等数据结构，控制通道和媒体通道都以统一模型承载。 | 功能代码应传递结构化事件和任务输入，不应直接拼接底层 JSON 或二进制协议。 |
| 非实时半双工语音链路 | 第 2 项；Phase C | 已有 `/ws_audio`、ASR、AgentCore、TTS `/stream.wav`、播放状态和麦克风关闭/恢复链路。 | 可用于普通问答和任务触发；不能把它当作实时打断语音。 |
| AgentCore 工具调用 | 第 4 项；Phase D | `AgentFacade`、`OpenAIAgentLoopRunner`、`ToolRegistry`、`SdkToolAdapter` 已能把 SDK `BaseTool` 暴露给模型调用。 | 功能能力应优先实现为 `BaseTool`，由 SDK 统一注册和注入上下文。 |
| Tool 与 MCP 基础接入 | 第 5 项；Phase E/H | SDK 已提供 `BaseTool`、`BaseMcpAdapter`、`McpRegistry`、`McpGateway` 和 `DeviceGroupContext.mcp`。 | 外部服务应通过 MCP Adapter 接入；具体地图厂商或业务服务不应硬编码进 SDK 核心。 |
| 最小 Skill Runtime | 复合能力扩展；Phase H/L | sdk-v10 已提供 `SkillRuntime`、`SkillManifest`、`SkillDocument`、`read_skill` 工具、会话 active Skill、prompt 注入、工具白名单和执行前校验。 | Skill 只描述复合任务流程和工具边界，真实执行仍通过 Tool、Task、MCP；业务可开始注册受控 Skill。 |
| 抓拍和图片资产主链路 | 第 6 项；Phase G | `capture_photo`、相机控制事件、图片资产回流、Agent 图片输入链路已经具备最小闭环。 | 需要拍照时使用 `context.capture_photo()` 或 SDK 工具，不要在业务层直接操作相机协议。 |
| SDK 托管业务 Task | 第 8 项；Phase F | `BaseTask`、`TaskRuntimeManager`、`TaskContext` 支持创建、查询、取消、事件分发、事件日志、超时和 JSON 快照恢复。 | 长流程业务应实现 `BaseTask`，不要自行开线程维护任务状态。 |
| 系统级 `phone_video_link_task` | 第 10 项；Phase J | SDK 内置 `phone_video_link_task`，支持 peer-link 准备、ready、streaming、停止、完成、取消、失败、超时等阶段。 | 功能代码通过 `context.start_phone_video_link()` 启停链路，不直接下发相机推流命令。 |
| 手机端多能力注册和任务插件入口 | 第 9/10 项；Phase I/J/K | iOS 侧已有 `PhoneTaskCapabilityRegistry`，Python SDK 侧已有 `BasePhoneTask`、`BasePhoneProcessor`、`PhoneRuntime` 和按任务分发视频帧能力。 | 真 iPhone 能力应写成 Swift 插件；Python 的 `BasePhoneTask` 更适合作为服务端、模拟器和回放侧抽象。 |
| 任务事件回流与通知仲裁 | Phase F/J/K/L | `TaskEventBridge` 可把任务事件转换为会话消息或通知请求，`NotificationCoordinator` 支持去重、排队、显式抢播策略、仲裁决策快照和高优先级通知中断旧通知。 | 业务只提交结构化通知和策略；当前抢播只覆盖通知播放流，不等于实时语音用户打断。 |
| 普通文本流式 TTS 透传与首包观测 | 第 2 项；Phase C | `OpenAIAgentLoopRunner` 已能把普通 `response.output_text.delta` 透传给 `VoiceRuntime` 的流式 TTS，会话快照记录首文本、首音频和首播放请求时间。 | 可用于普通问答降低首音频等待时间；如果 TTS 回退全文合成，首包延迟仍会如实暴露在运行态中。 |
| 设备级回放和联调工具 | 各阶段测试要求 | `glass-playback`、`phone-mock`、preflight、协议契约测试和 live-check 已支持基本自动化验证。 | 功能开发应优先用回放工具复现链路问题；完整断言和真实视频回放仍有不足。 |
| 配置、日志、异常和预检 | Phase A | `ServerSettings`、日志配置、统一错误模型、CLI 预检和运行态诊断已经覆盖基础开发调试。 | 排障日志应使用 DEBUG 级别，业务不应绕过 SDK 自行散落底层诊断输出。 |

## 存在欠缺但可以继续开发的能力

| 欠缺能力 | 当前 SDK 状态 | 对功能开发的影响 | 建议处理方式 |
| --- | --- | --- | --- |
| 真实地图和 AMap MCP 治理 | SDK 有 MCP Gateway 和 Adapter 机制，但没有内置真实 AMap 适配器、鉴权、限流、重试、超时和 mock 切换策略。 | 第 7 项导航可继续通过 MCP Adapter 接入，但业务团队需要明确依赖外部服务的输入输出边界。 | SDK 后续应提供地图 Adapter 模板、配置约定、错误分类和测试替身。 |
| 导航任务通用模板 | SDK 能托管 `BaseTask`，但没有内置导航状态机、路线阶段模型、偏航处理、视觉事件融合策略。 | Phase L 可以实现业务 `navigation_task`，但不同导航能力之间可能重复维护任务状态。 | SDK 后续应沉淀导航类长任务模板和标准任务事件。 |
| 手机视觉执行框架的资源管理 | sdk-v6 已在 Python `PhoneRuntime` / `phone-mock` 中支持 `vision_policy`、帧率限制、最大帧数和过载事件，但真 iOS 运行时还没有统一模型资源池、任务抢占和功耗治理。 | Phase K 的视觉迁移可用 mock/回放验证资源策略；真机多视觉任务并发时仍需谨慎。 | 功能开发先使用 `vision_policy` 表达资源要求；SDK 后续补 iOS 通用资源调度层。 |
| 完整播放仲裁和用户语音打断 | sdk-v8 已支持通知层去重、排队、抢播策略和中断旧通知，但普通 Agent 回复、任务通知、视觉告警、用户语音打断之间还没有完整统一仲裁。 | 任务通知和高优先级告警已有 SDK 入口；实时语音打断和多源恢复播放仍不能承诺。 | 业务侧只发结构化事件、优先级和 `interrupt_policy`，不要直接控制播放器或实时收音。 |
| 任务持久化生产化 | sdk-v5 支持 JSON 快照导出、保存和恢复，但不是多进程、多实例、数据库级任务持久化。 | 开发和单机回放足够，服务重启和线上长任务恢复仍有风险。 | SDK 后续应提供数据库存储、幂等事件、任务恢复和过期清理机制。 |
| Skill Runtime 产品化治理 | sdk-v10 已有最小 Skill Runtime，但还没有远程 Skill Registry、审批、风险等级、目录扫描和复杂会话恢复。 | 业务可用显式注册的本地 Skill；暂不能把 Skill 当成线上动态配置平台。 | 功能团队先用 `register_skill` 注册受控 Skill；远程管理和审批写入 SDK 后续优化。 |
| 回放测试断言能力 | 当前 `glass-playback` 和 `phone-mock` 能跑通设备级链路，但缺少更完整的批量用例、结果断言、真实视频帧回放和失败报告。 | 功能团队可以做自动化冒烟和人工判读，但难以覆盖复杂回归。 | SDK 后续应把回放升级为可声明预期结果的测试框架。 |
| iOS 和 ESP32 SDK 打包形态 | iOS 当前更接近 App 工程内 SDK 代码，尚未形成 XCFramework/SPM；ESP32 侧也不是独立发布级组件。 | 内部联调可用，但外部开发者安装体验和版本隔离不足。 | SDK 后续应补正式包管理、版本说明和示例工程。 |
| 账号权限、组织管理和远程配置中心 | sdk-v9 已有账号级设备索引和跨账号绑定隔离，但没有完整授权、审计、组织树和远程配置中心。 | 多设备能力开发可先按 `account_id` 使用 SDK 快照；涉及权限和后台管理仍不能承诺。 | 功能团队不要在业务 Task 中自建权限体系；需要产品级权限时写入 SDK 阻塞文档。 |

## 当前不能支持的能力

| 不能支持的能力 | 对应计划 | 原因 | 功能开发团队处理方式 |
| --- | --- | --- | --- |
| 实时语音对话和用户打断 | 第 3 项 | 当前主链路是非实时半双工，播放期间关闭麦克风；没有完整实时模型、回声消除、VAD 打断、TTS 中断和状态仲裁。 | 不要在业务层实现临时实时语音协议；应作为 SDK 独立大版本能力。 |
| 公网或复杂网络下稳定的眼镜和手机直连 | 第 10 项扩展场景 | `phone_video_link_task` 是最小链路任务，不包含 NAT 穿透、TURN/STUN、中继、弱网重连和跨网安全策略。 | 只在 SDK 明确支持的局域网或当前联调网络内开发；公网能力写入阻塞文档。 |
| 生产级账号权限、设备目录和审计后台 | 第 1/9 项产品化扩展 | sdk-v9 提供运行时账号索引，不提供持久化设备目录、授权、审计和管理后台。 | 功能团队不应在业务 Task 中自行实现权限或组织后台。 |
| 完整迁移旧视觉业务工作流 | Phase K/L 的后续范围 | SDK 提供承载框架，不内置盲道、路口、找物、导航等具体算法和业务流程。 | 业务团队只迁移具体能力；SDK 只负责任务、设备、上下文、事件和测试支撑。 |
| 分布式高可用任务运行时 | Phase F 生产化扩展 | 当前任务运行时以单进程内存和 JSON 快照为主，不支持多实例抢占、分布式锁、事件日志回放和数据库事务。 | 线上部署前需要 SDK 专项增强，业务层不应复制一套任务平台。 |

## 按第一期功能编号的支持等级

| 功能编号 | 功能摘要 | SDK 支持等级 | 说明 |
| --- | --- | --- | --- |
| 1 | 眼镜和服务端注册、配对 | 已经能够支持 | 控制通道、设备注册、心跳、绑定和运行态诊断已具备。 |
| 2 | 非实时语音对话 | 已经能够支持 | 半双工语音、ASR、AgentCore、TTS 播放链路可用。 |
| 3 | 实时语音对话和打断 | 不能支持 | 缺少完整实时语音和中断仲裁链路。 |
| 4 | AgentCore 工具调用 | 已经能够支持 | SDK Tool 可以暴露给 AgentCore。 |
| 5 | Tool 和 MCP | 已经能够支持基础能力 | MCP 网关和 Adapter 机制可用，真实服务治理仍需补强。 |
| 6 | 抓拍照片和图片理解 | 已经能够支持 | 抓拍请求、图片回流和模型图片输入具备主链路。 |
| 7 | AMap MCP 导航 | 存在欠缺 | SDK 有 MCP 接入能力，但没有内置真实地图 Adapter 和导航状态模板。 |
| 8 | 后端任务管理 | 已经能够支持 | `BaseTask` 和 `TaskRuntimeManager` 已覆盖创建、查询、取消、事件和快照。 |
| 9 | 手机注册和眼镜手机绑定 | 已经能够支持 | phone 注册、自动绑定和设备组上下文已经具备。 |
| 10 | 手机视频链路后台任务 | 已经能够支持最小闭环 | `phone_video_link_task`、peer-link 事件、相机推流启停已具备；公网和弱网能力不足。 |
| K | 手机视觉执行框架 | 存在欠缺 | 任务插件和帧分发入口具备，资源管理和结果协议仍需沉淀。 |
| L | 视觉导航迁移 | 存在欠缺 | SDK 可承载导航任务，但缺少导航专用模板、事件规范和通知仲裁。 |

## 给功能开发团队的使用建议

1. 短动作能力优先实现为 `BaseTool`，例如查询、一次性调用外部服务、触发抓拍。
2. 长流程能力优先实现为 `BaseTask`，例如导航、持续视觉检测、后台视频链路。
3. 外部系统优先通过 `BaseMcpAdapter` 接入，业务代码不要直接依赖 SDK 内部运行时对象。
4. 需要硬件能力时，通过 `DeviceGroupContext` 获取眼镜、手机、相机、任务和 MCP 能力，不要绕过 SDK 控制通道。
5. 真 iPhone 上的端侧能力应使用 Swift 侧 `PhoneTaskCapabilityRegistry` 扩展；Python 侧手机抽象主要用于服务端、mock 和回放。
6. 自动化测试优先使用 `glass-playback`、`phone-mock` 和 SDK 的任务快照能力；测试能力不够时记录为 SDK 改进建议。
7. 遇到实时语音、公网直连、复杂抢播、账号权限、生产级持久化等问题时，不要在业务层临时补系统能力，应写入“架构阻塞点说明与改进建议”。

## SDK 后续优先优化方向

1. 为手机视觉任务补资源调度、结果协议、帧率控制和并发策略。
2. 提供导航类长任务模板，统一路线状态、视觉事件、提示策略和取消恢复语义。
3. 提供地图 MCP Adapter 模板，明确配置、鉴权、超时、重试、mock 和错误分类。
4. 把任务快照升级为生产级持久化，支持数据库存储、幂等事件和服务重启恢复。
5. 把回放工具升级为可声明预期结果的自动化测试框架。
6. 扩展 Skill Runtime 产品化治理：目录扫描、风险等级、审批、远程注册和复杂会话恢复。
7. 以独立大版本实现实时语音、用户打断、播放中断和多源提示仲裁。
8. 补齐 iOS、ESP32 和 Python SDK 的正式打包、安装和版本兼容说明。
