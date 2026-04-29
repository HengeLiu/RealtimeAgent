# SDK 对功能开发支持情况的说明

更新时间：2026-04-29

本文只记录当前 `openaiglass-sdk` 对 `openaiglass-for-blind` 业务能力开发的真实支撑面。旧的阶段计划已经删除，历史推进过程以 `openaiglass-sdk/docs/stage2/iteration-v*.md` 为准。

## 总体结论

当前 SDK 已经可以支撑 `find_object`、`traffic_light`、`navigation`、`timer` 四类业务样板继续开发。业务代码应优先放在 `openaiglass-for-blind/capabilities`，通过 SDK 公开的 `BaseTool`、`BaseTask`、`BasePhoneTask`、`BasePhoneProcessor`、`DeviceGroupContext`、`context.mcp(...)` 和回放工具完成能力实现与验证。

SDK 已经提供的主能力包括：

| 能力 | 当前支持情况 | 业务使用边界 |
| --- | --- | --- |
| 设备注册、心跳、绑定和运行态诊断 | `/ws/control` 支持 glass、phone 注册、心跳、自动绑定、账号字段透传和 `/api/runtime/devices` 快照。 | 业务代码只查询设备组上下文，不自行维护设备表或绑定表。 |
| 半双工语音主链路 | `/ws_audio`、ASR、AgentCore、TTS `/stream.wav`、播放状态和麦克风恢复链路可用。 | 普通问答和任务触发可直接使用；业务层不改语音协议。 |
| 全双工实时语音第一版 | `VOICE_SESSION_MODE=full_duplex_realtime`、`voice.realtime.*`、`RealtimeVoiceRuntime`、用户插话事件、播放仲裁和回放级验证已落地。 | 仍依赖端侧 AEC/VAD 和真实模型适配效果；业务只声明语音输入或通知输出，不实现实时语音底座。 |
| AgentCore Tool 调用 | SDK `BaseTool` 可通过 `OpenAIAgentLoopRunner` 暴露给模型调用，系统工具和业务工具统一注册。 | 新短动作能力优先实现为 `BaseTool`。 |
| MCP 接入 | `BaseMcpAdapter`、`McpMethodSpec`、`CapabilityResult` 已从 `openaiglasses` 公开入口导出，`DeviceGroupContext.mcp(...)` 是业务调用入口。 | 真实地图、搜索等外部服务通过业务 adapter 接入，不硬编码进 SDK 核心。 |
| 抓拍和图片资产 | `capture_photo` 通过控制面请求眼镜抓拍，图片资产回流后进入模型图片输入链路。 | 业务用 `context.capture_photo()` 或系统工具，不直接拼相机控制消息。 |
| 手机视频链路 | `DeviceGroupContext.start_phone_video_link(...)` / `stop_phone_video_link(...)` 已进入 SDK 标准系统任务入口，预检会检查适配器绑定。 | `find_object`、`traffic_light` 等视频能力通过公开上下文启动链路，不调用 debug API。 |
| 手机任务和视觉资源 | Python `BasePhoneTask` / `BasePhoneProcessor` 服务 mock、回放和契约验证；真 iOS 侧通过 `PhoneTaskCapabilityRegistry` 插件承载业务任务，通用运行时包含视觉资源协调。 | 不把 Python phone 代码当作真实 iPhone 插件入口。 |
| SDK 托管任务 | `BaseTask`、`TaskRuntimeManager`、事件日志、快照恢复、SQLite 存储和单机租约可用。 | 长流程能力优先实现为 `BaseTask`，不在业务侧自建线程或任务数据库。 |
| 通知和播放仲裁 | `NotificationCoordinator`、`PlaybackArbiter`、用户主动打断和任务事件通知桥接已可用。 | 业务提交结构化通知、优先级和策略，不直接控制播放器。 |
| Skill Runtime | 本地受控 Skill、`read_skill`、工具白名单和会话 active Skill 已有最小实现。 | 可用于受控复合能力说明，不能当作远程动态 Skill 平台。 |
| 回放和预检 | `phone-mock`、`glass-playback`、`openaiglass.sdk.preflight`、`live-check`、音频样例回归和业务验收脚本可用。 | 单元测试通过后，跨设备能力应尽量跑设备级回放。 |

## 仍需注意的边界

| 边界 | 说明 | 建议 |
| --- | --- | --- |
| 真实地图服务治理 | 当前业务侧是 `MockAmapMcpAdapter`，没有真实 AMap 鉴权、限流、重试、超时和配额治理。 | 接真实地图前先定义 adapter 输入输出、错误分类和测试替身。 |
| 导航执行期模板 | SDK 承载 `navigation_task`，但不内置偏航、路线阶段、最后 10 米和多视觉事件融合模板。 | 先在业务能力中收敛最小策略，再判断是否沉淀为 SDK 模板。 |
| 全双工真机效果 | 协议和服务端运行时已具备第一版，但真实体验依赖端侧 AEC/VAD、实时模型和弱网表现。 | 真机验收时重点看 `voice.realtime.*`、播放仲裁和首包日志。 |
| 跨机器高可用任务 | 当前 SQLite 和租约面向单机或单机多进程，不是跨主机分布式任务平台。 | 多服务器部署前接外部数据库或专用任务平台，不在业务层复制一套。 |
| iOS/ESP32 发布形态 | 当前是源码包清单和 package-check，不是 XCFramework、Swift Package binaryTarget 或 ESP-IDF component registry。 | 内部联调按源码集成；正式外部分发另做发布方案。 |

## 功能开发建议

1. 短动作能力写成 `BaseTool`，长流程能力写成 `BaseTask`。
2. 需要眼镜、手机、相机、视频链路、通知、任务或 MCP 时，只通过 `DeviceGroupContext`。
3. 真实 iPhone 能力写 Swift 插件；Python phone 代码只用于 mock、回放和测试。
4. 排障先跑 `openaiglass.sdk.preflight`、业务单元测试和 `glass-playback` 回放，再进入真机联调。
5. 如果发现 SDK 公开能力不足，把问题写入 `openaiglass-for-blind/docs/stage1/develop/架构阻塞点说明与改进建议.md`，不要在业务目录补系统底座。
