# SDK 对功能开发支持情况的说明

更新时间：2026-04-27  
评估对象：`sdk-v3`  
评估范围：`docs/stage1/plan` 下第一期 1-10 项、Phase A-L，以及当前 `openaiglass-sdk` / `openaiglass-for-blind` 实现。

## 1. 总体结论

当前 SDK 已经能够支撑业务团队按 `BaseTool`、`BaseTask`、`BasePhoneProcessor`、`BasePhoneTask`、`MCP Adapter` 和 `ScenarioRunner` 开发多数 Stage1 最小闭环能力。对业务开发最重要的几个系统性问题已经由 SDK 承接：

1. 眼镜、手机、服务端注册和心跳。
2. 眼镜与手机一对一绑定。
3. 统一控制消息、媒体帧、设备组上下文。
4. 服务端 Tool / Task 注册和 Agent 调用。
5. `context.capture_photo(...)`、`context.start_phone_video_link(...)`、`context.start_phone_task(...)`、`context.submit_notification(...)`、`context.mcp(...)` 等高层能力。
6. 离线场景回放、mock 设备、预检和真机配置检查。

但 SDK 仍更接近“最小可用开发框架”，还不是完整产品级三端运行时。真实 AMap、实时语音打断、复杂任务调度、通知抢占、手机端本地视觉模型运行框架、iOS 插件自动纳入宿主工程、ESP32 组件化发布等能力仍有欠缺或暂不能由业务层单独完成。

## 2. 已经能够支持的功能

以下能力已经可以按现有 SDK 公开接口完成业务开发，业务层不需要改 SDK。

| 计划项 | SDK 支持情况 | 当前依据 |
| --- | --- | --- |
| Phase A：三端基础目录、配置、日志、协议测试 | 已支持服务端 SDK 包、iOS 通用运行时、ESP32 工程、协议/配置/错误/日志测试 | `openaiglass-sdk/server-python`、`phone-ios`、`glass-esp32`、`tests/unit`、`tests/contracts` |
| Phase B：眼镜注册、pair_token 校验、心跳、重连、注册后开语音会话 | 已支持 | `api/ws/control_runtime.py` 处理 `device.register`、`device.heartbeat`、重连覆盖、心跳超时、`voice.session.open` |
| Phase C：非实时语音对话主链路 | 已支持最小闭环 | `runtime/voice_runtime.py` 支持 `/ws_audio`、音频段聚合、ASR、Agent、TTS、`/stream.wav` 下发 |
| Phase D：agent-core 最小运行时 | 已支持 | `AgentSession / AgentTurn / AgentTurnResult`、`OpenAIAgentLoopRunner`、SDK Tool 适配到 Agent |
| Phase E：Tool Registry / Tool Gateway / MCP Gateway | 已支持 | `OpenAIGlassesSDK.register_tool()`、`register_mcp_adapter()`、`DeviceGroupContext.mcp()` |
| Phase F：业务 Task 模板开发 | 已支持创建、查询、取消、事件推进的最小闭环 | `BaseTask`、`TaskContext`、`TaskRuntimeManager`、`backend_task_core`；业务侧 `timer_task` 已验证 |
| Phase G：单次抓拍和图片解读 | 已支持最小闭环 | `context.capture_photo()`、`sensor.camera.capture/captured`、图片资产引用和 Agent 图片解读路径 |
| Phase H：导航准备、mock AMap、导航任务最小闭环 | 已支持业务 mock 闭环 | `context.mcp()` 可调用业务注册的 `MockAmapMcpAdapter`；业务侧 `navigation_task` 已验证 |
| Phase I：手机注册到服务端，手机与眼镜绑定 | 已支持最小闭环 | iOS 运行时发送 `device.register/heartbeat`，服务端自动或显式绑定，并维护设备组 |
| Phase J：手机接收眼镜视频流并回显 | 已支持最小数据面 | ESP32 响应 `sensor.camera.stream.start/stop`，iOS `/ws/camera` 接收 `MediaFrame(camera_frame)` |
| Phase K：手机端业务插件样板 | 已支持多插件按 `taskType` 注册与分发 | `PhoneTaskCapabilityRegistry.register(taskType:)`、`PhoneCapabilityBootstrap` |
| Phase L：导航任务接收视觉事件的最小策略 | 已支持业务层实现 | 业务侧 `navigation_task` 可接收 `phone.vision.traffic_light.result` 并提交通知 |
| 离线场景回放与预检 | 已支持 | `ScenarioRunner.run/validate/describe`、`MockGlassRuntime`、`MockPhoneRuntime`、`run_sdk_preflight.py` |

当前已落地业务能力包括：

1. `find_object`：Tool、Task、PhoneProcessor、PhoneTask、iOS 插件样例和回放场景。
2. `traffic_light`：红绿灯识别 Tool、Task、PhoneProcessor、PhoneTask、iOS 插件样例和回放场景。
3. `navigation`：导航准备、POI 候选、地理编码、路线 mock、导航任务、红绿灯视觉事件接入和回放场景。
4. `timer`：计时器创建、查询、取消、完成事件回放和通知。

## 3. 存在欠缺但可继续开发的功能

以下能力 SDK 已有可用骨架，业务层可以继续推进最小闭环，但要接受当前限制，或在实施文档中明确联调边界。

| 功能 | 当前欠缺 | 对业务开发的影响 | 建议 |
| --- | --- | --- | --- |
| 实时语音对话与打断 | 当前主链路仍以非实时语音段为核心；播放中持续收音、打断、半双工/全双工状态切换不完整 | 第一期第 3 项不能按电话式实时交互验收 | SDK 团队补实时音频会话、播放抢占、VAD/唤醒协同和状态机 |
| 普通文本回复端到端流式 TTS | Agent 有流式观察路径，CosyVoice 也有流式 TTS，但普通回复不是所有文本 delta 都稳定透传到播放 | 长回复首音频延迟和可打断体验不足 | 继续收敛 `agent-core -> voice-runtime -> TTS -> /stream.wav` 增量链路 |
| `backend-task-core` 产品级任务运行时 | 最小任务可创建/查询/取消/派发事件；缺少持久化、定时调度、统一状态迁移强校验、超时恢复 | `timer` 真实自然到点、长时导航、断线恢复只能先做 mock 或运行期内存实现 | SDK 增加通用定时事件、任务持久化和恢复机制 |
| 任务事件回流 AgentTurn | 有事件桥接和通知能力，但 Agent 对后台事件的主动再推理策略还不完整 | 高优先级事件可通知，复杂“先回流 Agent 再决定播报”还不稳定 | 明确事件优先级、直达/回流策略和上下文写入规范 |
| 通知抢占与去重 | `submit_notification(priority=...)` 可用，但复杂抢播、排队、去重、跨任务冲突策略仍弱 | 手机视觉、导航、Agent 可能重复播报或抢占规则不清 | SDK 提供 `NotificationCoordinator` 的公开策略配置和测试夹具 |
| 真实 AMap 接入 | 当前业务侧只有 mock adapter，SDK 只提供 MCP 网关，不提供真实地图 adapter | 导航可做准备闭环，但不能验收真实路线和地图错误处理 | 业务侧可先写 adapter；SDK 需沉淀真实外部服务配置、超时、鉴权和错误码规范 |
| `phone_video_link_task` | SDK 有系统任务和控制消息联动，但 peer link 协议仍是最小直连方案 | 第 10 项可最小演示，但缺少完整 prepare/ready/failed/close 生命周期 | SDK 补正式 `peer_link` 控制语义、超时和失败回收 |
| 手机端视觉能力迁移 | iOS 能接帧、分发任务、上报结果；但没有统一本地模型管理、YOLO 运行、模型资源下载和性能策略 | `find_object`、`traffic_light` 可做样板，盲道/避障等复杂能力还需要大量业务实现 | SDK 或业务宿主补手机端视觉模型执行框架与性能观测 |
| iOS 业务插件集成 | SDK 支持多插件注册，但业务 Swift 文件仍需手动加入 Xcode target，未形成 Swift Package/XCFramework | 新能力接入真机时仍有工程操作成本 | SDK 团队提供插件自动纳入或包化方案 |
| ESP32 眼镜端扩展 | 已支持音频、抓拍、视频流、通知等通用能力；但不是 ESP-IDF component，新增硬件能力需改 SDK 工程 | 业务团队不能自行扩展新硬件能力，只能提需求 | SDK 团队组件化眼镜运行时并固化硬件能力扩展流程 |
| 真机联调自动化 | 已有配置同步、live check、启动脚本；但真实 iPhone/ESP32 权限、网络抖动、端口占用仍需人工观察 | 可以开始真机测，但失败诊断仍依赖日志经验 | 增强联调状态页、端到端链路自检和设备侧日志导出 |

## 4. 当前不能支持或不应由业务层支持的功能

以下功能不能仅靠 `openaiglass-for-blind` 业务层完成。若计划要求继续推进，应先由 SDK 团队补公开能力或明确框架设计。

| 功能 | 不能支持的原因 | 需要 SDK 团队提供的能力 |
| --- | --- | --- |
| 完整实时语音打断 | 涉及眼镜持续收音、播放状态、服务端实时模型会话、VAD、抢占播放和恢复监听，不是业务 Tool/Task 能表达的问题 | 实时语音 runtime、双工状态机、打断协议和端侧实现 |
| 完整 `peer_link.prepare/ready/failed/close` 协议 | 当前视频链路是服务端协调后的最小直连任务，不具备完整 peer link 生命周期协议 | 标准 peer-link 控制消息、状态机、超时、重试和可观测性 |
| 真实手机端多视觉任务并发策略 | iOS 当前按活跃任务分发视频帧，缺少多任务共享帧、优先级和资源仲裁 | 手机任务调度器、帧分发策略、模型资源管理和性能保护 |
| 复杂导航执行期产品策略 | 最后 10 米、盲道、避障、过街、地图进度和视觉事件融合需要跨任务调度和安全策略 | 导航执行期通用状态机、传感器融合接口、通知抢占策略 |
| 业务层直接新增眼镜硬件能力 | 眼镜端属于通用 SDK 运行时，业务层不应直接改 `glass-esp32` 写业务策略 | 标准控制消息、服务端上下文方法、ESP32 通用能力实现 |
| SDK 发布级手机集成形态 | 当前 iOS 不是 Swift Package 或 XCFramework，业务工程通过 Xcode 引用源码 | SDK 包化、版本兼容、插件发现和宿主自动装配 |
| SDK 发布级眼镜集成形态 | 当前眼镜 SDK 是完整 ESP-IDF 工程，不是可被业务工程依赖的 component | ESP-IDF component 化、配置注入和硬件抽象边界 |
| 跨进程/重启后的任务恢复 | 当前业务任务主要是内存态或场景回放态 | 任务持久化、事件日志、恢复策略和幂等执行 |

## 5. 按 Stage1 功能项的支持度结论

| 原始功能项 | 支持级别 | 说明 |
| --- | --- | --- |
| 1. 眼镜与服务器配对注册 | 已经能够支持 | 注册、token 校验、心跳、重连、在线状态已由 SDK 承接 |
| 2. 非实时语音对话 | 已经能够支持 | 可完成音频段输入、ASR、Agent、TTS、播放闭环 |
| 3. 实时语音对话和打断 | 不能支持 | 需要 SDK 级实时音频和打断状态机 |
| 4. AgentCore 调工具 | 已经能够支持 | SDK Tool 可暴露给 Agent 调用 |
| 5. Tool 与 MCP | 已经能够支持 | `context.mcp(...)` 和 MCP trace 已可用 |
| 6. 拍照工具和图片解读 | 已经能够支持 | `capture_photo` 与图片资产链路已可用 |
| 7. AMap 导航能力 | 存在欠缺 | mock 和任务闭环可用，真实 AMap 与复杂执行期不足 |
| 8. 后台任务管理 | 存在欠缺 | 最小任务可用，真实定时、持久化、恢复和事件回流策略不足 |
| 9. 手机注册、配对、绑定 | 已经能够支持 | iOS 注册、心跳、绑定、状态查询已具备 |
| 10. 大模型创建眼镜手机直连视频任务 | 存在欠缺 | 最小视频链路可用，完整 peer-link 协议和异常恢复不足 |

## 6. 对后续 SDK 完善的建议优先级

1. 优先补实时语音打断 runtime，因为它是第一期第 3 项的硬缺口，业务层无法绕开。
2. 补强任务运行时：定时事件、持久化、状态迁移校验、任务事件回流 Agent 的策略。
3. 固化 peer-link 协议，让第 10 项从“能启动视频流”升级为“可诊断、可恢复、可取消的直连任务”。
4. 提供手机端插件集成自动化或包化方案，减少业务能力每次接入 iOS target 的人工操作。
5. 抽象手机端视觉执行框架，包括模型加载、帧分发、性能限制、结果上报和多任务仲裁。
6. 为真实外部服务 adapter 提供标准配置、鉴权、超时、错误码和 mock/real 切换规范，优先覆盖 AMap。
7. 将 ESP32 眼镜运行时逐步组件化，明确业务新增硬件能力时的 SDK 扩展流程。

## 7. 对业务开发的当前建议

1. `find_object`、`traffic_light`、`timer`、`navigation` 的最小业务闭环可以继续按现有 SDK 开发和真机联调。
2. 不要在业务层自行实现三端框架能力，例如实时语音打断、peer-link 协议、任务持久化、眼镜硬件抽象。
3. 对真实 AMap、复杂导航执行期、多视觉任务并发等能力，业务侧可以先做 mock 与最小策略，遇到 SDK 公开能力不足时再更新阻塞点文档。
4. 每个新增能力仍应至少提供成功、失败、取消或事件推进三类离线场景，先用 `ScenarioRunner` 验证，再进入真机联调。
