# Omni Server 与 Text Server 模态隔离设计

更新时间：2026-05-04

## 1. 背景

当前 SDK 的语音服务端已经支持两类模型调用：

1. Omni Realtime：眼镜音频流直接进入 Qwen Omni Realtime WebSocket，由 Omni 负责语义 VAD、转写、工具调用和音频输出。
2. Text Agent：眼镜音频先经过 ASR 变成文本，再进入文本大模型 Agent Call，最后通过 TTS 播放。

这两类链路现在共用 `VoiceRuntime`、`AgentFacade` 和 `OpenAIAgentLoopRunner` 的大量热路径。随着连续对话、拍照工具、停止对话、sidecar ASR、日志收口和播放仲裁不断叠加，代码概念已经变得混合：

1. Omni 链路希望遵循官方 Realtime 长连接范式，少做前置裁决，把原始音频和工具事件交给模型。
2. Text 链路必须有前置 ASR，适合做系统层意图识别、确定性过滤、文本 Agent 编排和 TTS。
3. 两者对 turn 的定义不同：Omni 的 turn 来自模型事件；Text 的 turn 来自端侧 `segment.finished + ASR`。
4. 两者对视觉的处理不同：Omni 应由模型调用 `capture_photo` 后在同一 Realtime 会话追加图片；Text 可以在 ASR 文本后做意图识别，再决定是否调用工具或进入图片解读链路。

因此需要在 SDK 概念和代码上明确拆分为两类 server：

1. `Omni Server`：全模态模型 server，严格对齐 Omni 官方 Realtime 直连方式。
2. `Text Server`：文本模型 server，走 ASR -> 意图/状态机 -> Text Agent Call -> TTS。

这里的 server 首先是 SDK 内部模型执行服务边界，不一定要求第一阶段拆成两个 OS 进程。最终可演进为两个独立进程或两个独立部署单元。

## 2. 设计目标

1. 模态边界清晰：Omni 代码不再混入 Text ASR 前置策略；Text 代码不再混入 Omni Realtime 事件细节。
2. 协议入口共享：眼镜、手机、控制 WebSocket、音频上传和播放下发协议不分叉，业务设备不需要知道后端选了哪类模型 server。
3. Agent 能力面复用：Tool、Task、Skill、MCP、Memory、DeviceGroupContext 仍是 SDK 统一能力，不复制业务扩展面。
4. Agent Call 适配器分离：Omni Agent Call 和 Text Agent Call 可以共享工具注册、提示词片段、上下文读取，但不能共用同一个模型运行循环。
5. 支持 A/B 与灰度：同一个设备组可以通过配置选择 `omni_server` 或 `text_server`，便于真机对比误触发、首响、token 消耗和业务正确性。
6. 降低认知负担：后续开发 Omni 能力时只看 Omni server；开发文本模型能力时只看 Text server。

## 3. 非目标

1. 不改变眼镜端控制协议和音频上传协议。
2. 不要求业务团队编写 `openaiglass-for-blind` 之外的 SDK 内部代码。
3. 第一阶段不强制拆成两个物理服务进程。
4. 不在 Text Server 中复刻 Omni semantic VAD。
5. 不在 Omni Server 中引入完整前置 ASR 作为主决策条件；sidecar ASR 只做日志、回填和低风险辅助。

## 4. 总体架构

```text
Glass / Phone
    |
    | control ws, audio ws, stream.wav
    v
ControlRuntime / DeviceRegistry / PlaybackGateway
    |
    v
VoiceGateway
    |
    +-- OmniVoiceServer
    |     |
    |     +-- OmniRealtimeSessionManager
    |     +-- OmniAgentAdapter
    |     +-- OmniToolBridge
    |     +-- OmniTurnRecorder
    |
    +-- TextVoiceServer
          |
          +-- AsrPipeline
          +-- TextDialogStateMachine
          +-- TextAgentAdapter
          +-- TtsPipeline
          +-- TextTurnRecorder

Shared SDK Core
    |
    +-- ToolRegistry / ToolGateway
    +-- SkillRuntime
    +-- MemoryRuntime
    +-- DeviceGroupRuntime
    +-- TaskRuntime
    +-- SessionStore / TurnCoordinator
```

核心拆分：

1. `ControlRuntime` 只负责设备连接、注册、绑定、控制消息路由。
2. `VoiceGateway` 只负责选择当前设备组使用哪个 `VoiceServer`。
3. `OmniVoiceServer` 和 `TextVoiceServer` 都实现统一接口，但内部模型链路完全分离。
4. 共享 SDK Core 只提供工具、任务、记忆、设备能力和会话记录，不关心底层模型是 Omni 还是文本。

## 5. 统一 VoiceServer 接口

新增内部协议：

```python
class VoiceServer(Protocol):
    def open_session(self, *, device_id: str, session_id: str, device_type: str) -> None: ...
    def close_session(self, *, device_id: str, session_id: str, reason: str) -> None: ...
    def on_segment_started(self, *, device_id: str, session_id: str, message: ControlMessage) -> None: ...
    def on_audio_frame(self, *, device_id: str, frame: MediaFrame) -> None: ...
    def on_segment_finished(self, *, device_id: str, session_id: str, message: ControlMessage) -> None: ...
    def on_playback_event(self, *, device_id: str, session_id: str, message: ControlMessage) -> None: ...
    def submit_notification(self, request: NotificationRequest) -> NotificationSubmitResult: ...
    def build_snapshot(self) -> dict[str, Any]: ...
```

`VoiceGateway` 按配置选择实现：

```yaml
voice:
  server_mode: omni_server   # omni_server | text_server
```

兼容旧配置：

| 旧配置 | 新映射 |
| --- | --- |
| `VOICE_REPLY_MODE=omni_realtime` | `voice.server_mode=omni_server` |
| `VOICE_REPLY_MODE=agent_tts` | `voice.server_mode=text_server` |
| `VOICE_INPUT_MODE=raw_audio` | 只允许 `omni_server` |
| `VOICE_INPUT_MODE=asr_text` | 只允许 `text_server` |

保留旧环境变量一个阶段，但启动时打印迁移提示。

## 6. Omni Server 设计

### 6.1 职责

Omni Server 只支持全模态 Realtime 模型。它的原则是让模型服务端承担模型层语义能力，SDK 不在模型前面再做一套复杂意图系统。

职责：

1. 管理设备语音会话级 Omni Realtime WebSocket。
2. 按官方方式执行 `session.update`、`append_audio`、`append_video`、`create_item`、`create_response`。
3. 使用 Omni `semantic_vad` 作为主 turn detection。
4. 处理 Realtime server events。
5. 把 `response.audio.delta` 写入统一播放流。
6. 把 `response.audio.done` 作为当前播放流收口信号。
7. 处理 Realtime function calling。
8. 将工具结果回填给 Omni。
9. 在 `capture_photo` 工具完成后，把图片追加到同一条 Omni 会话。
10. 把 Omni transcript、assistant text、工具轨迹和音频资产写回 SessionStore。

### 6.2 不做什么

1. 不在调用 Omni 前等待完整 ASR。
2. 不用 ASR 关键词判断视觉意图。
3. 不在 SDK 内部先判断“本轮是否要拍照”。
4. 不在每轮回复后关闭 Omni 连接。
5. 不通过 Text Agent 的流式事件观察器处理 Omni server events。

### 6.3 Omni Agent Call

Omni Agent Call 不应等同于 Text Agent Call。它应该是 Realtime Agent Adapter：

```text
Omni server event
  -> OmniAgentAdapter
  -> ToolGateway / Memory / Skill prompt fragment
  -> function_call_output
  -> Omni create_response
```

它复用：

1. `ToolRegistry`：生成 Realtime function schema。
2. `ToolGateway`：执行工具。
3. `SkillRuntime`：提供系统提示词片段和工具可见性。
4. `MemoryRuntime`：提供基础信息、主题和 memory tools。
5. `SessionStore`：记录最终 transcript 和 assistant text。

它不复用：

1. Text Agent 的 ASR 后消息构造。
2. Text Agent 的 Chat Completions / Agents SDK stream observer。
3. Text Agent 的 TTS 管线。
4. Text Agent 的前置意图识别状态机。

### 6.4 Omni Server 状态机

```text
IDLE
  -> OPENING_MODEL_SESSION
  -> LISTENING_REALTIME
  -> MODEL_RESPONDING
  -> PLAYING_REPLY
  -> LISTENING_REALTIME
  -> CLOSING
  -> IDLE
```

说明：

1. `LISTENING_REALTIME` 期间音频持续进入 Omni。
2. `MODEL_RESPONDING` 由 `response.created` 或工具调用事件触发。
3. `PLAYING_REPLY` 由首个 `response.audio.delta` 触发。
4. `response.audio.done` 只结束当前播放流，不关闭模型 session。
5. `close_continuous_dialog`、端侧 `voice.dialog.close`、控制连接断开或不可恢复错误才进入 `CLOSING`。

### 6.5 Omni 工具策略

默认模型可见系统工具：

1. `capture_photo`
2. `close_continuous_dialog`
3. `memory_search`
4. `manage_memory`
5. `read_skill`
6. 当前 Skill/MCP 允许的业务工具

工具调用前置播报仍由 SDK 统一处理，但逻辑属于 Omni server 的工具桥，不与 Text server TTS 预热混用。

`capture_photo` 的 Omni 特殊处理：

```text
response.function_call_arguments.done(name=capture_photo)
  -> ToolGateway.invoke(capture_photo)
  -> function_call_output 写回 Omni
  -> append_video(image_bytes)
  -> create_response(TEXT, AUDIO)
```

## 7. Text Server 设计

### 7.1 职责

Text Server 支持纯文本或文本主导的大模型。它适合可解释、可控、规则明确的系统层流程。

职责：

1. 接收端侧一段完整音频。
2. 执行实时 ASR 或批量 ASR，得到文本。
3. 执行文本层对话状态机和意图识别。
4. 进入 Text Agent Call。
5. 执行文本模型工具调用。
6. 将最终文本流送入 TTS。
7. 通过统一播放流下发 TTS 音频。
8. 记录 ASR 文本、意图决策、模型请求、工具轨迹和 TTS 资产。

### 7.2 Text Dialog StateMachine

Text Server 可以保留系统层意图识别，因为它的输入已经是稳定文本：

```text
ASR_TEXT_READY
  -> STOP_COMMAND
  -> WAKE_ONLY_OR_FILLER
  -> ASSISTANT_ECHO
  -> VISUAL_QUERY
  -> NORMAL_QUERY
  -> TASK_CONTROL
```

建议策略：

1. 停止对话、静音、取消任务等高确定性控制可以由 SDK 前置处理。
2. 明显空文本、语气词、助手回声可以丢弃。
3. 视觉查询可以有两种模式：
   - `tool_first`：Text Agent 自己调用 `capture_photo`。
   - `router_first`：状态机确认视觉意图后直接构造带图片的 Text Agent 输入。
4. 默认推荐 `tool_first`，保持和 Omni Server 概念一致；`router_first` 只作为低延迟优化或旧能力兼容。

### 7.3 Text Agent Call

Text Agent Call 使用文本消息作为主输入：

```text
messages = [
  system,
  memory_context,
  skill_context,
  history text,
  current user ASR text,
]
```

如果模型调用 `capture_photo`：

```text
tool_called(capture_photo)
  -> ToolGateway.invoke(capture_photo)
  -> image asset
  -> image follow-up Text Agent Call
  -> TTS
```

Text Agent 可以继续使用现有 `OpenAIAgentLoopRunner`，但应重命名为 `TextAgentRunner` 或拆出 `TextAgentAdapter`，避免它继续承担 Omni Realtime 原生音频职责。

### 7.4 Text Server 状态机

```text
IDLE
  -> CAPTURING_SEGMENT
  -> ASR_RUNNING
  -> INTENT_ROUTING
  -> TEXT_AGENT_RUNNING
  -> TTS_STREAMING
  -> PLAYING_REPLY
  -> IDLE
```

## 8. 共享能力层设计

两类 server 不能复制业务扩展能力。应抽出 `AgentCapabilityRuntime`：

```python
@dataclass
class AgentCapabilityRuntime:
    tool_registry: ToolRegistry
    tool_gateway: ToolGateway
    skill_runtime: SkillRuntime | None
    memory_runtime: AgentMemoryRuntime | None
    device_group_runtime: DeviceGroupRuntime
    task_gateway: TaskGateway
    session_store: AgentSessionStore
    turn_coordinator: TurnCoordinator
```

Omni 和 Text 分别使用不同 adapter：

```text
AgentCapabilityRuntime
  -> OmniAgentAdapter
  -> TextAgentAdapter
```

适配器差异：

| 能力 | OmniAgentAdapter | TextAgentAdapter |
| --- | --- | --- |
| 输入 | raw audio / image / Realtime event | ASR text / optional image |
| 模型调用 | DashScope Omni Realtime | Chat/Responses/Agents SDK |
| 工具 schema | Realtime function schema | Text function tool schema |
| 工具结果 | `function_call_output` item | Agent tool output / follow-up |
| 输出 | audio delta + transcript | text delta |
| 播放 | 模型原生音频 | TTS |

## 9. 配置设计

新增配置：

```yaml
voice:
  server_mode: omni_server

models:
  omni:
    provider: dashscope
    model: qwen3.5-omni-plus-realtime
    url: wss://dashscope.aliyuncs.com/api-ws/v1/realtime
    turn_detection:
      type: semantic_vad
      threshold: 0.5
      silence_duration_ms: 700
      prefix_padding_ms: 300
    session_lifecycle: persistent

  text:
    provider: dashscope_openai
    model: qwen-plus
    stream: true

asr:
  provider: dashscope
  model: fun-asr-realtime
  mode: realtime

tts:
  provider: dashscope
  model: cosyvoice-v2
  voice: longxiaochun

text_server:
  intent_router:
    enabled: true
    visual_strategy: tool_first
    suppress_empty: true
    suppress_assistant_echo: true

omni_server:
  sidecar_asr:
    enabled: true
    use_for_decision: false
```

兼容规则：

1. `voice.server_mode=omni_server` 时禁止 `voice_input_mode=asr_text`。
2. `voice.server_mode=text_server` 时禁止 `voice_input_mode=raw_audio`。
3. `omni_server.sidecar_asr.use_for_decision=false` 是默认值。
4. 旧 `VOICE_REPLY_MODE` 可映射到 `voice.server_mode`，但新代码内部不再使用 reply mode 作为分支核心。

## 10. 目录与代码拆分

建议新增目录：

```text
server-python/
  runtime/
    voice_gateway.py
    voice_server_base.py
    omni/
      omni_voice_server.py
      omni_realtime_session.py
      omni_agent_adapter.py
      omni_tool_bridge.py
      omni_event_mapper.py
    text/
      text_voice_server.py
      asr_pipeline.py
      text_dialog_state_machine.py
      text_agent_adapter.py
      tts_pipeline.py
    playback/
      playback_gateway.py
      playback_stream_store.py
```

迁移后：

1. `VoiceRuntime` 缩小为兼容 facade，内部委托 `VoiceGateway`。
2. 新功能只进 `runtime/omni` 或 `runtime/text`。
3. `agent_core/runtime/runner.py` 不再包含 Omni Realtime 原生音频 runner。
4. Omni Realtime DashScope SDK 事件只存在于 `runtime/omni`。
5. ASR/TTS 供应商细节只存在于 `runtime/text`。

## 11. 协议与观测

控制协议继续共用：

1. `voice.realtime.session.open`
2. `sensor.audio.segment.started`
3. `audio_chunk`
4. `sensor.audio.segment.finished`
5. `assistant.reply`
6. `actuator.audio.play`
7. `voice.dialog.close`

新增观测字段：

```json
{
  "voice_server_mode": "omni_server",
  "model_session_id": "omni_xxx",
  "turn_source": "omni_semantic_vad",
  "agent_adapter": "omni_agent_adapter",
  "tool_schema_mode": "realtime_function",
  "asr_role": "sidecar_log_only"
}
```

Text Server 示例：

```json
{
  "voice_server_mode": "text_server",
  "turn_source": "segment_finished",
  "agent_adapter": "text_agent_adapter",
  "tool_schema_mode": "text_function",
  "asr_role": "primary_input"
}
```

## 12. 测试策略

### 12.1 单元测试

Omni Server：

1. 持久 Realtime session 多轮复用。
2. `response.audio.done` 只关闭播放流，不关闭模型连接。
3. `capture_photo` 工具输出图片后追加 `append_video`。
4. `close_continuous_dialog` 播报完成后关闭模型连接和端侧窗口。
5. sidecar ASR 不阻塞 Omni。

Text Server：

1. ASR 文本为空时抑制。
2. 停止指令前置关闭。
3. 文本意图路由的视觉策略。
4. Text Agent tool call 后进入图片 follow-up。
5. TTS 首包和播放流收口。

共享能力：

1. 同一 ToolRegistry 在两类 adapter 下导出不同 schema。
2. Skill 白名单对两类 server 一致生效。
3. Memory tools 在两类 server 中一致可见。

### 12.2 回放测试

新增回放 profile：

```text
audio-samples/
  omni_server/
  text_server/
```

同一批音频分别跑：

1. 普通问答。
2. 视觉问答。
3. 停止对话。
4. 背景噪声。
5. 助手回声。
6. 工具调用。

输出对比：

1. 首响延迟。
2. ASR 耗时。
3. 工具调用次数。
4. 图片调用次数。
5. token / audio duration。
6. 是否误回复。

### 12.3 真机测试

必须覆盖：

1. `omni_server` 下连续多轮追问。
2. `omni_server` 下“看一下眼前有什么”由模型调用 `capture_photo`。
3. `omni_server` 下“现在几点了”不调用 `capture_photo`。
4. `text_server` 下 ASR -> 文本 Agent -> TTS 全链路。
5. 两类 server 都能响应 `close_continuous_dialog`。
6. 切换配置后不需要改眼镜固件。

## 13. 分阶段实施计划

实施状态：

1. `sdk-v97` 已完成 Phase 1 的配置与内部协议边界。
2. `sdk-v98` 已完成 Phase 2/3/4 的第一轮代码落点：新增 `OmniVoiceServer`、`TextVoiceServer`、`TextDialogStateMachine` 和 package-check 导入覆盖。为保护已验证的真机语音链路，DashScope Realtime 客户端和 TTS/ASR 热路径暂时仍由 `VoiceRuntime` 承载，再由两个 server adapter 委托；后续只做低风险迁移，不改变设备协议。
3. `sdk-v99` 已完成第一轮物理拆分：Omni Realtime 客户端迁入 `runtime/omni/realtime_client.py`，ASR/TTS/兼容语音模型客户端迁入 `runtime/text/speech_clients.py`，共享常量、模型分片和模型载荷解析迁入独立模块；`VoiceRuntime` 保留兼容导入并继续承载设备会话编排。

### Phase 1：抽象边界

1. 新增 `VoiceServer` 协议和 `VoiceGateway`。
2. 将当前 `VoiceRuntime` 的公共控制入口委托到 `VoiceGateway`。
3. 保持行为不变。
4. 增加 `voice.server_mode` 配置并映射旧 `VOICE_REPLY_MODE`。

验收：

1. 现有单测全部通过。
2. 真机行为不变。

### Phase 2：抽出 Omni Server

1. 将 `DashscopeOmniRealtimeReplyClient`、`OmniRealtimeStreamingSession`、Omni server event callback 移入 `runtime/omni`。
2. 将 Realtime tool bridge 移入 `OmniToolBridge`。
3. `VoiceRuntime` 不再直接引用 DashScope Omni SDK。
4. Omni session 生命周期只由 `OmniVoiceServer` 管理。

验收：

1. `omni_server` 下多轮连续对话复用同一 Omni 连接。
2. `capture_photo` Realtime 工具图片追加仍通过。

`sdk-v99` 当前落地：

1. 新增 `runtime/omni/omni_voice_server.py`，建立 Omni Server 适配器。
2. `VoiceGateway.from_runtime(...)` 在 `voice.server_mode=omni_server` 时选择 `OmniVoiceServer`。
3. `VoiceRuntime` snapshot 增加 `voice_server_mode`，方便联调确认当前模型服务。
4. `DashscopeOmniRealtimeReplyClient`、`OmniRealtimeStreamingSession`、`OmniRealtimeReplyResult` 和 Omni server event 摘要逻辑已迁入 `runtime/omni/realtime_client.py`。
5. `VoiceRuntime` 仍负责设备会话、播放流和 Task/通知编排；下一轮再继续拆播放、通知和会话状态。

### Phase 3：抽出 Text Server

1. 将 ASR、文本意图、Text Agent、TTS 移入 `runtime/text`。
2. 将 `OpenAIAgentLoopRunner` 收敛为 `TextAgentAdapter`。
3. 明确 `TextDialogStateMachine` 的规则边界。
4. Text Server 不再依赖 Omni Realtime 类。

验收：

1. `text_server` 下普通问答、视觉问答、停止对话和工具调用通过。
2. Text Server 可以用不支持音频输入的纯文本模型。

`sdk-v99` 当前落地：

1. 新增 `runtime/text/text_voice_server.py`，建立 Text Server 适配器。
2. 新增 `runtime/text/text_dialog_state_machine.py`，把停止指令、空文本、语气词、助手回声和短连续 VAD 文本规则收敛到 Text Server 状态机。
3. `VoiceRuntime` 的文本裁决路径改为调用 `TextDialogStateMachine`，Omni 主链路仍不等待完整 ASR 做主裁决。
4. `VoiceModelClient`、`DashscopeVoiceModelClient`、`SpeechRecognitionClient`、`DashscopeSpeechRecognitionClient`、`StreamingTtsSession`、`DashscopeCosyVoiceTtsSession` 和实时 ASR 会话已迁入 `runtime/text/speech_clients.py`。

### Phase 4：清理旧分支

1. 废弃 `VOICE_REPLY_MODE` 内部主分支。
2. 删除 `voice_input_mode=auto` 中的隐式交叉逻辑。
3. 删除 `AgentFacade` 中 Omni 原生音频 runner 兼容路径。
4. 文档和配置全部切到 `voice.server_mode`。

验收：

1. 新增 SDK package-check 确认 Omni 代码不 import Text ASR/TTS。
2. Text 代码不 import Omni Realtime SDK。

`sdk-v99` 当前落地：

1. 内部热路径继续以 `effective_voice_server_mode()` 作为主分支。
2. `VOICE_REPLY_MODE` 保留为迁移兼容字段；如果和 `VOICE_SERVER_MODE` 同时配置且不一致，启动会失败。
3. package-check 增加新 server 边界模块和物理拆分模块导入验证。
4. `runtime.voice_runtime` 保留旧类名 re-export，避免业务测试替身和已有单测在迁移期被迫改导入路径。

## 14. 风险与取舍

1. Tool/Memory/Skill 复用如果抽象过重，会拖慢第一阶段。建议先做 adapter 分离，不急于重写整个 AgentCore。
2. Omni Server 的工具调用仍需要和 SDK ToolGateway 强耦合，这是合理耦合，不能让业务工具绕开 SDK。
3. Text Server 的意图识别会重新引入规则系统，但它只存在于文本模型链路，不再和 Omni 竞争。
4. 两类 server 的历史上下文格式不同。SessionStore 应记录统一审计视图，但不强迫模型请求格式一致。
5. 如果未来 Omni 官方支持更完整 Agent SDK，`OmniAgentAdapter` 可以替换内部实现，不影响 Text Server。

## 15. 最终形态

最终 SDK 对业务开发者呈现为：

```yaml
voice:
  server_mode: omni_server
```

或：

```yaml
voice:
  server_mode: text_server
```

业务能力仍只写：

1. Tool
2. Task
3. Skill
4. DeviceGroupContext 调用
5. 回放测试

模型链路差异由 SDK server mode 吸收。Omni 模型开发和文本模型开发在代码路径上独立演进，互不影响；共享的只有 SDK 能力契约和设备协议。
