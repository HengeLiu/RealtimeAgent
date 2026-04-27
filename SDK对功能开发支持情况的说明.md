# SDK 对功能开发支持情况的说明

更新时间：2026-04-27  
评估对象：`sdk-v3`  
评估范围：本目录下 `第一期功能开发计划.md`、`第一期前三项开发落地计划.md`、`第二阶段第4-8项开发落地计划.md`、`第三阶段第9-10项与视觉导航迁移开发落地计划.md`。

## 1. 评估结论

当前 SDK 已经从早期脚手架推进到“可支撑 Stage1 多数最小闭环业务开发”的状态。业务团队可以基于 `BaseTool`、`BaseTask`、`BasePhoneProcessor`、`BasePhoneTask`、`DeviceGroupContext`、`MCP Adapter` 和 `ScenarioRunner` 开发找物、红绿灯、计时器、导航准备等能力，不需要直接处理三端 WebSocket、设备绑定、任务状态表、视频控制消息和场景回放机制。

但当前 SDK 还不是完整产品级三端运行时。第一期计划中的实时语音打断、完整 peer-link 生命周期、真实地图接入、复杂导航执行期、多视觉任务并发、任务持久化恢复、手机/眼镜 SDK 包化等仍存在明显缺口。这些缺口大多属于 SDK 框架问题，不适合业务团队在 `openaiglass-for-blind/capabilities` 内自行补系统层实现。

## 2. 已经能够支持的功能

以下功能已有 SDK 公开接口或通用运行时承载，业务开发团队可以按当前开发指南继续实现和扩展。

| 功能或计划项 | 当前支持判断 | 主要依据 |
| --- | --- | --- |
| 三端目录、配置、日志、错误码和协议公共层 | 已支持 | SDK 已拆出 `server-python`、`phone-ios`、`glass-esp32`，并提供配置、日志、错误码、`ControlMessage`、`MediaFrame`、契约测试。 |
| 眼镜与服务器配对注册 | 已支持 | 服务端控制运行时处理 `device.register`、`device.heartbeat`、pair token、重连覆盖、心跳离线；眼镜端会响应 `voice.session.open`。 |
| 手机注册与眼镜手机一对一绑定 | 已支持 | iOS 运行时可注册和心跳；服务端维护眼镜、手机设备组；开发者可通过 `DeviceGroupContext.require_phone()`、`query_devices()` 获取绑定设备。 |
| 非实时语音对话主链路 | 已支持 | 服务端已有 `/ws_audio`、语音段聚合、ASR、agent-core、TTS、`/stream.wav` 下发；眼镜端支持唤醒、录音、播放和恢复监听。 |
| AgentCore 调用 Tool | 已支持 | SDK 提供 `AgentFacade`、`AgentTurn`、`ToolRegistry`、`ToolGateway`，业务 Tool 可通过 `OpenAIGlassesSDK.register_tool()` 注入模型工具面。 |
| Tool 与 MCP 基础能力 | 已支持 | `OpenAIGlassesSDK.register_mcp_adapter()` 与 `DeviceGroupContext.mcp(...)` 已可用，业务 Tool 不需要自行拼装 `McpRegistry / McpGateway`。 |
| 单次抓拍和图片解读 | 已支持 | `capture_photo` 通过 `CameraGateway -> ControlRuntime -> sensor.camera.capture / sensor.camera.captured` 完成新抓拍，并进入图片理解链路。 |
| 业务 Task 最小开发面 | 已支持 | 业务可扩展 `BaseTask`，通过 `context.create_task()`、`query_task()`、`cancel_task()` 和 `TaskContext` 管理任务状态。 |
| 手机侧持续任务承载 | 已支持 | `BasePhoneProcessor`、`BasePhoneTask`、`PhoneRuntime`、iOS `PhoneTaskCapabilityRegistry` 已支持按 `taskType` 注册和分发手机业务插件。 |
| 眼镜到手机视频链路最小闭环 | 已支持 | `start_phone_video_link()` / `stop_phone_video_link()` 可下发 `sensor.camera.stream.start/stop`，iOS 端可通过 `/ws/camera` 接收 JPEG 帧并回显。 |
| 找物能力最小闭环 | 已支持 | `find_object` 已有 Tool、Task、PhoneProcessor、PhoneTask、iOS 插件样例和多条回放场景。 |
| 红绿灯识别最小闭环 | 已支持 | `traffic_light` 已有服务端 Task、手机侧处理器、iOS 插件样例和红/绿/缺手机/取消等场景。 |
| 导航准备与导航任务样板 | 已支持最小闭环 | `navigation` 已有 `prepare_navigation`、mock AMap MCP、POI 候选确认、导航任务进度与红绿灯视觉事件接入。 |
| 计时器任务样板 | 已支持最小闭环 | `timer` 已有创建、运行 tick、取消、完成通知等场景；适合作为后台任务业务样板。 |
| 离线场景回放 | 已支持 | `ScenarioRunner.run/describe/validate`、mock 设备和 `run_sdk_scenario.py` 已能覆盖成功、失败、取消和事件推进。 |
| SDK 边界预检 | 已支持 | `run_sdk_preflight.py` 可组合编译、边界、包检查、场景、契约、兼容性和健康检查。 |

## 3. 存在欠缺但仍可继续推进的功能

以下功能已有 SDK 骨架或最小路径，可以继续做业务最小闭环，但不能直接按产品级完整能力验收。

| 功能或计划项 | 当前欠缺 | 对功能开发的影响 | 建议处理 |
| --- | --- | --- | --- |
| 普通语音回复的端到端流式播放 | Agent 和 TTS 都有流式相关实现，但普通文本 delta 到 TTS 播放的全链路还不够稳定 | 长回复首音频延迟和打断体验会受影响 | SDK 继续收敛 `agent-core -> voice-runtime -> TTS -> /stream.wav` 增量链路。 |
| 后台任务运行时产品化 | 当前能支持业务 Task 最小闭环，但缺少任务持久化、自然到点调度、重启恢复、超时统一治理 | `timer` 可回放验证，真实长时间运行和重启恢复不足 | SDK 增加通用定时事件、任务持久化和恢复策略。 |
| 任务事件回流 Agent 再决策 | 当前可以发布事件和提交通知，但“任务事件回到 Agent 形成上下文决策”的策略还不完整 | 高优先级事件能通知，复杂任务总结、追问和去重仍弱 | SDK 明确事件优先级、直达端侧、回流 Agent 和上下文写入规则。 |
| 通知抢占、排队和去重 | `submit_notification(priority=...)` 可用，服务端也有通知协调雏形，但跨任务抢播策略还不够产品化 | 手机视觉、导航和普通问答同时播报时可能互相打断或重复 | SDK 提供稳定 `NotificationCoordinator` 策略配置和回放测试夹具。 |
| AMap 真实接入 | SDK 提供 MCP 网关，业务侧已有 mock adapter，但真实 AMap 鉴权、限流、错误码和网络超时未形成标准 | 导航准备能做 mock 闭环，不能代表真实地图验收 | 短期由业务注册真实 adapter；SDK 沉淀外部服务 adapter 配置、mock/real 切换和错误规范。 |
| `phone_video_link_task` 完整任务语义 | 当前可启动视频链路和系统任务，但 peer-link 生命周期较薄 | 第 10 项能最小演示，失败、超时、对端离线、关闭回收不够清晰 | SDK 补正式 `peer_link.prepare/ready/failed/close` 协议和状态机。 |
| 手机端视觉能力迁移 | iOS 能接帧、按任务分发和上报结果，但没有统一模型资源、YOLO 运行、性能限制和多任务帧共享框架 | `find_object`、`traffic_light` 可作为样板，盲道、避障等复杂能力迁移成本仍高 | SDK 或手机宿主补视觉执行框架、模型资源管理、帧分发和性能观测。 |
| 导航执行期任务 | 业务侧已有最小 `navigation_task` 和视觉事件接入，但复杂路径进度、盲道、红绿灯、避障融合还不充分 | 可以做“可运行后台任务”，但不能宣称完整室外导航产品化 | 先保持业务最小策略，SDK 补传感器、视觉事件、地图进度和通知策略的标准接口。 |
| iOS 业务插件集成 | SDK 支持多插件注册，但业务 Swift 文件仍需要工程层加入 target | 新增能力真机验证时仍有手工工程操作 | SDK 后续提供 Swift Package、XCFramework 或插件自动纳入方案。 |
| 眼镜端工程复用 | ESP32 工程已经是通用运行时，但还不是 ESP-IDF component | 新硬件能力不能由业务团队直接扩展，只能改 SDK 工程 | SDK 团队组件化眼镜运行时，并固化硬件能力扩展流程。 |
| 真机联调自动化 | 已有同步配置、live check、run 脚本和状态接口，但跨设备失败仍依赖人工看日志 | 可以开始三端联调，稳定性和失败定位仍有成本 | 增强端侧日志导出、链路自检、网络诊断和真机测试报告模板。 |

## 4. 当前不能支持的功能

以下功能不能只靠业务能力层实现。若后续计划继续推进，应先由 SDK 团队补框架能力或重新定义验收范围。

| 功能或计划项 | 当前不能支持的原因 | 需要 SDK 补齐的内容 |
| --- | --- | --- |
| 第一项第 3 点：电话式实时语音对话和用户打断 | 这涉及眼镜持续收音、播放期 VAD、实时模型会话、半双工/全双工切换、播放抢占和恢复监听，业务 Tool/Task 无法表达 | 实时语音 runtime、实时 ASR/LLM/TTS 或全双工会话、打断控制协议、端侧状态机和回归测试。 |
| 完整 peer-link 生命周期协议 | 当前视频链路更接近“服务端下发启动/停止 + 眼镜向手机推流”的最小实现，尚未形成 prepare/ready/failed/close 的标准协议闭环 | 标准 peer-link 消息、超时、重试、失败回收、运行态查询和端侧确认机制。 |
| 多视觉任务并发与资源仲裁 | iOS 运行时按当前活跃任务投递帧，缺少多个视觉任务共享帧、优先级、模型资源和功耗控制 | 手机端任务调度器、帧分发策略、模型资源管理、并发限制和性能保护。 |
| 完整室外导航产品策略 | 最后 10 米、岔路、安全过街、盲道、避障、地图进度和视觉事件融合不是单个业务 Task 能稳定兜住的问题 | 导航执行期标准状态机、视觉/地图/传感器融合接口、安全通知策略和真机验收套件。 |
| 业务层直接新增眼镜硬件能力 | 眼镜端属于通用 SDK 运行时，业务目录不应直接改 `glass-esp32` 写业务策略或硬件协议 | SDK 公开新的 `DeviceGroupContext` 方法、控制消息、ESP32 通用硬件能力和联调测试。 |
| SDK 发布级 iOS 集成形态 | 当前仍以源码/Xcode 工程方式复用，不具备正式包依赖、版本兼容和插件发现机制 | Swift Package 或 XCFramework、资源注入、插件发现、版本兼容规则。 |
| SDK 发布级 ESP32 集成形态 | 当前是完整 ESP-IDF 工程，不是可被外部业务工程依赖的 component | ESP-IDF component 化、Kconfig/环境配置注入、硬件抽象边界和发布说明。 |
| 跨服务重启的任务恢复 | 当前任务和会话主要是内存态，离线场景可以回放，但不是生产级持久恢复 | 任务事件日志、会话/任务持久化、幂等恢复和补偿执行机制。 |

## 5. 按第一期 1-10 项的支持级别

| 原始功能项 | 支持级别 | 判断说明 |
| --- | --- | --- |
| 1. 眼镜与服务器配对并注册 | 已经能够支持 | 注册、token 校验、心跳、重连和运行态查询已在 SDK 运行时内承接。 |
| 2. 非实时语音对话 | 已经能够支持 | 可完成唤醒后音频段上传、ASR、Agent、TTS 和播放闭环。 |
| 3. 实时语音对话，支持用户打断 | 不能支持 | 当前没有完整实时语音会话和播放期持续收音打断状态机。 |
| 4. 引入 AgentCore 调用工具 | 已经能够支持 | SDK Tool 可注册并暴露给 agent-core。 |
| 5. 引入工具与 MCP | 已经能够支持 | Tool/MCP 注册、调用和 `context.mcp(...)` 已形成公开入口。 |
| 6. 拍照工具和图片解读 | 已经能够支持 | `capture_photo`、图片资产引用和图片理解路径已具备。 |
| 7. 基于 AMap MCP 的导航能力 | 存在欠缺 | mock 导航准备和任务闭环可用，真实 AMap、复杂澄清和执行期策略不足。 |
| 8. 后台任务管理工具 | 存在欠缺 | 业务 Task 最小闭环可用，持久化、自然调度、恢复和事件回流策略不足。 |
| 9. 手机设备注册、配对、绑定 | 已经能够支持 | iOS 注册、心跳、绑定、视频接收和状态查询已具备。 |
| 10. 大模型创建手机与眼镜直连后台任务 | 存在欠缺 | 最小视频链路可用，完整 peer-link 生命周期、失败恢复和任务治理不足。 |

## 6. 对功能团队的开发建议

1. `find_object`、`traffic_light`、`timer`、`navigation` 的最小业务闭环可以继续基于现有 SDK 开发、回放和真机联调。
2. 新业务优先使用 `BaseTool`、`BaseTask`、`BasePhoneProcessor`、`BasePhoneTask`、`context.mcp(...)`、`context.start_phone_video_link(...)`、`context.start_phone_task(...)` 等公开入口。
3. 遇到实时语音打断、peer-link 生命周期、任务持久化、手机视觉并发、眼镜硬件扩展等问题时，不要在业务能力里重复实现 SDK 框架能力，应记录到架构阻塞点文档。
4. 对真实 AMap、复杂导航、多视觉能力迁移，可以先通过 mock 和最小策略推进回放闭环，同时把真实联调所需的 SDK 缺口拆成明确问题。
5. 每个新增能力至少补成功、失败、取消或事件推进三类回放场景，先通过 `ScenarioRunner` 和 `run_sdk_preflight.py`，再进入真机联调。

## 7. 对 SDK 团队的后续优先级建议

1. 优先补第一期第 3 项所需的实时语音打断 runtime，这是当前最明确的“不能支持”项。
2. 补强后台任务运行时：定时事件、任务持久化、状态迁移校验、超时和重启恢复。
3. 固化 peer-link 协议，让第 10 项从“能启动视频流”升级为“可观察、可恢复、可取消的直连任务”。
4. 抽象手机视觉执行框架，包括模型加载、帧分发、多任务仲裁、性能限制和结果上报。
5. 提供真实外部服务 adapter 的标准配置、鉴权、超时、错误码和 mock/real 切换规范，优先覆盖 AMap。
6. 推进 iOS SDK 和 ESP32 SDK 的发布级包化，降低业务能力接入真机工程的人工成本。
