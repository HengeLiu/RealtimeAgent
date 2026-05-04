# Omni Server 与 Text Server 拆分验收文档

更新时间：2026-05-04

## 1. 验收基线

本次拆分的验收基线是：以开始拆分 Omni Server 和 Text Server 之前的 SDK 外部行为作为最低验收状态。也就是说，拆分可以改变内部模块边界和文件组织，但不能让三端协议、设备联调、业务开发 API、Omni 连续对话、Text ASR 链路、Tool/Task/Skill/MCP 能力出现回退。

当前可追溯的拆分前参考提交是：

```text
e8e38f2 修复唤醒提示音I2S恢复
```

从该状态开始，后续 `sdk-v97` 到 `sdk-v103` 都属于本轮 Omni Server / Text Server 拆分和语音运行时瘦身过程。

## 2. 总体验收目标

1. 行为不回退：眼镜、手机、服务端的控制协议、音频协议、播放协议和业务 Tool/Task/Skill/MCP API 不因拆分变化。
2. 模态边界清晰：Omni Realtime 代码归属于 `runtime/omni`；Text ASR/TTS/Text Agent 代码归属于 `runtime/text`。
3. 共享能力复用：ToolRegistry、TaskRuntime、SkillRuntime、MemoryRuntime、DeviceGroupContext、播放仲裁、通知协调等 SDK Core 不复制两套。
4. 配置可灰度：`voice.server_mode=omni_server|text_server` 是主配置，旧 `VOICE_REPLY_MODE` 只作为迁移兼容。
5. 可测试可观测：package-check、单测、回放测试和真机联调都能证明两类 server 的关键行为。
6. 可维护：`voice_runtime.py` 不再承载全部客户端、状态、播放、通知和模型管线；新增模块职责能从文件名直接看出。

## 3. 不允许回退的外部行为

### 3.1 三端协议

验收项：

1. 眼镜端仍使用现有 `/ws/control`、`/ws_audio`、`/stream.wav` 和 `voice.realtime.*` 控制语义。
2. 手机端注册、心跳、绑定、相机接收和手机任务协议不变化。
3. 服务端 `ControlRuntime` 对设备注册、心跳、绑定、播放、通知、Task 事件的处理行为不变化。

验收方法：

1. package-check 通过。
2. 设备级回放能完成注册、语音输入、模型回复和播放下发。
3. 真机联调中不需要修改 ESP32 固件或 iOS 业务宿主配置。

### 3.2 Omni Server 行为

验收项：

1. `voice.server_mode=omni_server` 时，默认走 Omni Realtime 原生音频链路。
2. Omni `semantic_vad` 继续作为主 turn detection，不等待完整旁路 ASR 才调用模型。
3. Omni persistent 长连接不在每轮回复后关闭；`response.audio.done` 只收口当前播放流。
4. 用户要求结束连续对话时，模型可调用 `close_continuous_dialog`，SDK 在当前回复播放完成后关闭连续窗口。
5. 视觉任务不再由 SDK 前置关键词判断是否拍照；模型需要画面时调用 `capture_photo`，SDK 抓拍后把图片追加给 Omni。
6. Tool 调用结果仍能通过 Realtime function calling 回填，工具前置播报不破坏最终回复。

验收方法：

1. 单测覆盖 `DashscopeOmniRealtimeReplyClient` 的 server event、工具调用、`response.audio.done`、semantic VAD 兜底和 `capture_photo` 图片追加。
2. 设备级回放覆盖普通问答、工具调用、视觉问答、停止连续对话。
3. 真机至少覆盖首次唤醒、多轮追问、停止对话、播放结束后继续追问和错误恢复。

### 3.3 Text Server 行为

验收项：

1. `voice.server_mode=text_server` 时，输入模式为 ASR 文本链路。
2. Text 链路支持 ASR -> TextDialogStateMachine -> Text Agent -> TTS。
3. Text 链路可以使用不支持音频输入的纯文本模型。
4. 停止指令、空文本、语气词、助手回声和短连续 VAD 文本的规则只属于 Text Server，不和 Omni 主链路竞争。
5. Text 链路仍能复用 SDK Tool、Task、Skill、MCP、Memory 和通知能力。

验收方法：

1. 单测覆盖 TextDialogStateMachine。
2. text_server 配置下跑普通问答、工具调用、停止对话和通知播报。
3. 使用纯文本模型配置运行不报 `raw_audio` 相关错误。

### 3.4 业务开发 API

验收项：

1. 业务开发者继续只扩展 `BaseTool`、`BaseTask`、Skill、MCP 配置和业务宿主配置。
2. 业务侧不需要关心当前底层是 Omni Server 还是 Text Server。
3. `openaiglass-for-blind` 业务代码不因 SDK 内部拆分需要修改。
4. `SDK安装与能力开发指南.md` 写明当前 SDK 版本和业务侧使用方式。

验收方法：

1. 业务侧测试不需要改 SDK 内部导入。
2. 指南版本与 `openaiglass-for-blind/sdk-version` 一致。

## 4. 内部结构验收标准

### 4.1 代码归属

最终目标：

| 模块 | 应承载内容 | 不应承载内容 |
| --- | --- | --- |
| `runtime/voice_gateway.py` | 选择 `OmniVoiceServer` 或 `TextVoiceServer` | 具体模型调用和播放细节 |
| `runtime/omni` | Omni Realtime 会话、server event、Realtime tool bridge、Omni turn recorder | Text ASR/TTS、TextDialogStateMachine |
| `runtime/text` | ASR、TextDialogStateMachine、Text Agent Adapter、TTS | Omni Realtime SDK、Omni server event |
| `runtime/audio_utils.py` | PCM/WAV、重采样等纯音频工具 | 设备会话状态和模型调用 |
| `runtime/voice_state.py` | 语音段、播放流、会话控制器等共享状态模型 | 具体模型调用和控制消息发送 |
| `runtime/voice_runtime.py` | 迁移期兼容门面和公共编排 | DashScope Realtime/ASR/TTS 客户端、复杂播放/通知/Task 管线 |

### 4.2 import 隔离

最终验收：

1. `runtime/omni` 不 import `runtime.text.speech_clients`。
2. `runtime/text` 不 import `runtime.omni.realtime_client`。
3. `runtime.voice_runtime` 可以在迁移期 re-export 旧名字，但最终不再是新增内部代码的默认导入入口。
4. package-check 显式导入所有拆分模块。

### 4.3 文件体积

目标不是机械追求最小行数，但单个文件不能再承载多个领域：

1. `voice_runtime.py` 最终应收敛到 1500 行以内，最好只保留设备会话门面和向新模块委托的代码。
2. Omni、Text、播放、通知、Task 事件、进度播报缓存应各自拥有独立模块或 service。
3. 单个模块超过 1500 行时，应再次检查是否混入多个职责。

## 5. 当前实现阶段评估

当前最新 SDK 文档版本为 `sdk-v103`。

| 阶段 | 目标 | 当前状态 | 完成度 |
| --- | --- | --- | --- |
| Phase 1 抽象边界 | `VoiceServer`、`VoiceGateway`、`voice.server_mode` | 已落地；旧 `VOICE_REPLY_MODE` 兼容映射仍保留 | 90% |
| Phase 2 抽出 Omni Server | Omni 客户端、工具桥、会话生命周期进入 `runtime/omni` | Omni Realtime 客户端已迁出；播放流队列和 HTTP 输出已迁入共享播放模块；进度播报缓存和通知/Task 语音桥接已独立；`OmniVoiceServer` 仍主要委托 `VoiceRuntime`；Realtime tool bridge 仍在客户端回调中；turn recorder 仍在 `VoiceRuntime` | 58% |
| Phase 3 抽出 Text Server | ASR、Text 状态机、Text Agent、TTS 进入 `runtime/text` | ASR/TTS 客户端和 TextDialogStateMachine 已迁出；进度播报 TTS 缓存和通知/Task 语音桥接已独立；Text Agent Adapter 尚未独立；`TextVoiceServer` 仍委托 `VoiceRuntime` | 55% |
| Phase 4 清理旧分支 | 废弃旧 reply mode 分支，收紧 import 隔离 | 主分支已使用 `effective_voice_server_mode()`；播放、进度缓存、通知桥接模块已进入 package-check；旧配置和旧导入仍保留；尚未做 import 规则断言 | 55% |

总体评估：当前属于“物理拆分后段”。最危险的客户端代码、播放子系统基础逻辑、进度播报缓存和通知/Task 语音桥接已经迁出，但设备会话和模型管线编排仍集中在 `VoiceRuntime`。距离最终验收大约还剩 30% 到 35% 的拆分工作，其中真正需要谨慎验证的是 Omni 工具桥、Text Agent Adapter 和真机连续对话行为。

## 6. 距离最终验收的剩余工作

### 6.1 必做

1. 拆 Omni tool bridge。
   - 把 Realtime function calling 的工具执行、`capture_photo` 图片追加、`close_continuous_dialog` 处理从 Realtime 客户端回调中收敛为 `OmniToolBridge`。
   - 风险：影响视觉问答和工具调用。
2. 拆 Text Agent Adapter。
   - 把 ASR 文本后的 Agent 消息构造、Text Agent 调用、TTS 合成收敛到 Text Server。
   - 风险：影响 text_server 兼容纯文本模型。
3. 增加 import 边界测试。
   - 用单测或 package-check 断言 Omni/Text 不互相 import 对方热路径。
   - 风险低。

### 6.2 可延后

1. 完全删除 `VOICE_REPLY_MODE`。
2. 完全删除 `runtime.voice_runtime` 旧类名 re-export。
3. 把 Omni Server 和 Text Server 拆成独立 OS 进程。
4. 做更细的 SessionStore / TurnRecorder 统一审计视图。

## 7. 下一步工作计划

### Milestone A：播放子系统独立（sdk-v101 已完成）

目标：

1. 已新增 `runtime/playback_streams.py`。
2. 播放流创建、播放请求、优先级排序、待播出队、中断标记、chunked WAV 输出和等待逻辑已迁出。
3. 普通回复、工具前置播报、通知播报、用户打断和播放完成事件行为通过单测保持不变。

建议验收：

1. `test_voice_runtime.py`、`test_realtime_voice_runtime.py` 和 `test_task_event_runtime.py` 已通过。
2. package-check 已覆盖 `runtime.playback_streams`。
3. 设备级回放仍归入 Milestone E 的最终验收矩阵统一执行。

### Milestone B：进度播报缓存独立（sdk-v102 已完成）

目标：

1. 已新增 `runtime/progress_audio_cache.py`。
2. 缓存 profile、WAV 读取、预热、淘汰和 PCM 查询逻辑已移出 `VoiceRuntime`。
3. 现有 `tools.progress_audio.*` 配置行为保持不变。

建议验收：

1. `test_voice_runtime.py`、`test_settings.py` 和 `test_agent_core.py` 已覆盖相关配置和缓存路径。
2. package-check 已覆盖 `runtime.progress_audio_cache`。

### Milestone C：通知与 Task 事件独立（sdk-v103 已完成）

目标：

1. 已新增 `runtime/notification_voice_bridge.py`。
2. 通知提交、直接播报、回流 Agent 决策和中断逻辑已移出 `VoiceRuntime`。
3. `DeviceGroupContext.submit_notification(...)` 仍进入同一个通知协调器和真实语音播放入口。

建议验收：

1. `test_task_event_runtime.py`、`test_voice_runtime.py` 和相关运行时单测已通过。
2. package-check 已覆盖 `runtime.notification_voice_bridge`。

### Milestone D：OmniToolBridge 与 TextAgentAdapter

目标：

1. 新增 `runtime/omni/tool_bridge.py`。
2. 新增 `runtime/text/text_agent_adapter.py`。
3. `OmniVoiceServer` 和 `TextVoiceServer` 不再只是包装同一个 `VoiceRuntime`，而是开始直接拥有各自模型管线。

建议验收：

1. Omni 工具调用、视觉问答、停止连续对话通过。
2. Text 普通问答、工具调用、停止对话、TTS 播放通过。

### Milestone E：边界收紧与最终验收

目标：

1. package-check 或单测断言 import 边界。
2. 文档全部以 `voice.server_mode` 为主，不再鼓励新代码使用 `VOICE_REPLY_MODE`。
3. 真机或设备级回放完成最终验收矩阵。

建议验收矩阵：

| 模式 | 用例 | 验收结果 |
| --- | --- | --- |
| `omni_server` | 普通问答 | 首响、播放完成、会话保持正常 |
| `omni_server` | 视觉问答 | 模型调用 `capture_photo` 后回答 |
| `omni_server` | 工具调用 | 工具结果回填并继续回复 |
| `omni_server` | 停止连续对话 | 当前回复结束后下发 `voice.dialog.close` |
| `text_server` | 普通问答 | ASR -> Agent -> TTS 正常 |
| `text_server` | 停止对话 | TextDialogStateMachine 生效 |
| 两种模式 | 通知播报 | `submit_notification` 进入真实播放 |
| 两种模式 | Task 终态 | 终态回流策略生效 |
