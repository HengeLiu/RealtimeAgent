# Realtime Audio Pipeline 详细设计

## 目标

本文定义服务器内部 `Realtime Audio Pipeline` 的完整设计，目标是让 Vision Realtime 和 Omni Realtime 两条链路对外表现为同一种实时音频对话能力。

端侧不应该知道服务器内部使用的是 Vision 模型链路还是 Omni 模型链路。对 `RealtimeAgentApp`、`ControlService`、`StreamService` 和端侧设备来说，两条链路都必须满足同一组外部行为：

1. 唤醒后建立一个 `audio_session`。
2. 建立独立的上行音频长连接和下行音频长连接。
3. 上行音频持续写入 pipeline。
4. pipeline 持续产出统一事件：用户开始说话、用户停止说话、下行音频、输出完成、输出取消、关闭会话等。
5. 下行水位控制通过统一接口暂停或恢复。
6. 连续对话关闭由服务器决定，端侧只执行关闭流程。

端侧标准见：[端侧音频交互标准.md](端侧音频交互标准.md)。

Vision 链路原始标准见：[视觉语言模型实时音频链路处理标准.md](视觉语言模型实时音频链路处理标准.md)。

## 设计原则

### 1. pipeline 对外统一

服务器外部调用方只能依赖 `RealtimeAgentRealtimePipeline` 这一层抽象，不直接依赖 `VisionRealtimeAgentCore`、`OmniAgentCore`、Paraformer、TTS provider 或 Omni provider 的原始事件。

Vision 和 Omni 的差异必须封装在各自 pipeline 内部：

| 链路 | pipeline 实现 | Agent Core | 输入边界来源 | 回复来源 |
| --- | --- | --- | --- | --- |
| Vision Realtime | `VisionRealtimePipeline` | `VisionRealtimeAgentCore` | `paraformer-realtime-v2` 句子事件 | Vision LLM + Streaming TTS |
| Omni Realtime | `OmniRealtimePipeline` | `OmniAgentCore` | Omni provider speech 事件 | Omni provider 音频输出 |

### 2. pipeline 内部允许差异，外部事件必须一致

Vision Realtime 是级联链路：

```text
mic audio -> Paraformer ASR -> VisionRealtimeAgentCore -> Vision LLM -> Streaming TTS -> speaker audio
```

Omni Realtime 是原生实时音频模型链路：

```text
mic audio -> Omni provider -> OmniAgentCore -> speaker audio
```

两者内部实现不同，但必须统一输出同一组 `PipelineEvent`，并接受同一组 pipeline 调用。

### 3. 连接绑定在进入 pipeline 前完成

`RealtimeAgentApp` 调用 `attach_upstream` 或 `attach_downstream` 之前，必须已经完成以下绑定：

1. 该连接属于哪个 `user_id`。
2. 该连接属于哪个 `device_id`。
3. 该连接属于哪个 `session_id`。
4. 该连接对应哪个 `stream_id`。
5. 该连接是上行麦克风还是下行扬声器。

这些是 WebSocket 建立阶段和 stream 注册阶段的职责。pipeline 热路径不重复做身份、session 或 stream 类型校验。

### 4. 打断是正常控制流

用户插话不是异常。无论 Vision 还是 Omni，只要 pipeline 判断用户开始说话，且当前存在正在生成或播放的助手输出，就必须执行统一打断语义：

1. 发布 `speech_started`。
2. 把当前 generation 已经生成的助手文本写入消息历史。
3. 在助手内容末尾追加 `<用户打断>`。
4. 取消当前模型响应或 provider 响应。
5. 取消当前 output stream。
6. 丢弃旧 generation 的后续回调。

旧 generation 的 LLM/TTS/provider 回调只能丢弃，不能写成 `response.failed`，也不能触发错误兜底音频。

### 5. finish 不等于 close

`output_finished` 只表示一段助手输出已经完整发送完毕。它不表示关闭下行音频长连接。

连续对话期间，下行连接必须保持打开，直到 pipeline 发出 `close_requested`，端侧完成本地播放 drain，并回报 `audio_session.closed`。

## 外部契约

### 统一接口

`RealtimeAgentRealtimePipeline` 对 `RealtimeAgentApp` 暴露以下接口：

| 方法 | 触发方 | 语义 |
| --- | --- | --- |
| `open_session(user_id, session_id)` | `RealtimeAgentApp` | 打开一次连续对话 session，初始化内部状态。 |
| `attach_upstream(stream_ref)` | `RealtimeAgentApp` | 绑定上行麦克风长连接，并预热输入 provider。 |
| `attach_downstream(stream_ref)` | `RealtimeAgentApp` | 绑定下行扬声器长连接，并预热输出 provider。 |
| `append_input_audio(chunk)` | `RealtimeAgentApp` | 持续写入上行麦克风音频。 |
| `pause_downstream()` | `RealtimeAgentApp` | 端侧下行队列达到高水位，暂停继续发送下行音频。 |
| `resume_downstream()` | `RealtimeAgentApp` | 端侧下行队列回落到低水位，恢复发送下行音频。 |
| `notify_output_finished(stream_id)` | `RealtimeAgentApp` | 端侧确认本段 output stream 已完成播放 drain。 |
| `detach_upstream(reason)` | `RealtimeAgentApp` | 上行连接关闭，释放输入 provider。 |
| `prepare_close(reason)` | `RealtimeAgentApp` | 准备关闭连续对话，停止接收新输出并 drain 或取消当前输出。 |
| `close_session(reason)` | `RealtimeAgentApp` | 关闭 session，释放 provider 和内部状态。 |

### 统一事件

pipeline 对 `RealtimeAgentApp` 只输出统一 `PipelineEvent`：

| 事件 | 语义 | 外部处理 |
| --- | --- | --- |
| `session_ready` | pipeline session 初始化完成。 | `RealtimeAgentApp` 可等待上下行连接。 |
| `upstream_ready` | 上行输入路径准备完成。 | 可持续转发麦克风音频。 |
| `downstream_ready` | 下行输出路径准备完成。 | 可接收助手音频输出。 |
| `speech_started` | 服务器判断用户开始说话。 | `ControlService` 下发给端侧；如有播放，端侧暂停写播放器并清空未播放队列。 |
| `speech_stopped` | 服务器判断用户停止说话。 | `ControlService` 下发给端侧；pipeline 内部启动或等待回复生成。 |
| `output_audio_delta` | 新的助手音频 chunk。 | `StreamService` 写入下行扬声器连接。 |
| `output_finished` | 一段助手输出发送完成。 | `StreamService` 发出 output finish 语义，等待端侧 drain 回执。 |
| `output_cancel_requested` | 当前助手输出需要取消。 | `StreamService` cancel 当前 output stream。 |
| `close_requested` | pipeline 请求关闭连续对话。 | `ControlService` 下发 `audio_session.close`。 |
| `session_closed` | pipeline 内部状态和 provider 已释放。 | `RealtimeAgentApp` 完成 session 清理。 |

### 外部契约时序

```plantuml
!include realtime-agent-realtime-pipeline-contract.puml
```

## 组件边界

### 外部共享组件

| 组件 | 职责 |
| --- | --- |
| `RealtimeAgentApp` | 应用编排入口，负责把控制事件、stream 事件和 pipeline 事件串起来。 |
| `ControlService` | 负责控制事件发布和端侧路由。 |
| `StreamService` | 负责 stream 生命周期、chunk 写入、finish、cancel 和端侧回执。 |
| `RealtimeAgentRealtimePipeline` | Vision / Omni 的统一接口，不暴露 provider 细节。 |

### pipeline 内部共享组件

| 组件 | 职责 |
| --- | --- |
| `RealtimeAudioNormalizer` | 处理音频格式适配、重采样和轻量诊断。它不做 VAD，也不决定 turn boundary。 |
| `RealtimeOutputController` | pipeline 内部共享输出控制器，负责下行水位、输出状态、finish/cancel 语义。实现时可以包装或复用现有 `OutputService`。 |
| `PipelineEventEmitter` | 统一输出 `PipelineEvent`，避免 Vision/Omni 原始事件穿透到外部。 |
| `RealtimeTurnState Base` | 共享 turn 状态、消息 buffer、generation id 和打断标记。 |

`RealtimeOutputController` 不是外层 `StreamService` 或 `ControlService` 的替代品。它只负责 pipeline 内部输出状态；真正写 WebSocket、发控制事件和接收端侧回执仍由外层服务完成。

### 组件复用与差异图

```plantuml
!include realtime-pipeline-component-comparison.puml
```

## VisionRealtimePipeline 设计

### 适用场景

`VisionRealtimePipeline` 用于视觉语言模型驱动的实时语音对话。它把实时麦克风音频转换成文本 turn，再由视觉语言模型生成回复，最后通过流式 TTS 生成下行音频。

### 内部组件

| 组件 | 职责 |
| --- | --- |
| `VisionRealtimePipeline` | 实现 `RealtimeAgentRealtimePipeline` 统一接口，协调 Vision 专属组件和共享组件。 |
| `VisionRealtimeAgentCore` | Vision 链路对话语义核心，负责上下文、消息历史、turn 状态、打断、关闭请求。 |
| `VisionInputBoundary` | 解释 Paraformer 事件，把句子事件转换成统一输入边界事件。 |
| `VisionResponseEngine` | 管理 Vision LLM 流式请求和 Streaming TTS。 |
| `Paraformer Realtime V2` | Vision 链路的 ASR 和用户语音边界来源。 |
| `Vision Model Provider` | 生成Vision 回复和 tool call。 |
| `Streaming TTS Provider` | 把文本 delta 合成下行音频 delta。 |

### 会话初始化

`open_session` 时，`VisionRealtimeAgentCore` 需要完成：

1. 编译当前会话上下文。
2. 初始化消息 buffer。
3. 初始化 generation id。
4. 设置状态为 `listening`。
5. 准备 `VisionInputBoundary` 和 `VisionResponseEngine`。

### 上行连接绑定

`attach_upstream` 时必须立即连接 `paraformer-realtime-v2`，并把 ASR 句子等待时间从配置传入 provider。

句子等待时间是配置项，不允许写死在代码里。它表达的是 provider 判断一句话结束的等待时间。

### 下行连接绑定

`attach_downstream` 时必须预热 Streaming TTS session，不能等到第一个文本 delta 到达时才创建 TTS 连接。

### 输入边界解释

Vision 链路不使用端侧 VAD。用户是否开始说话、停止说话由 Paraformer 返回的句子事件解释得到：

| Paraformer 事件 | VisionInputBoundary 输出 |
| --- | --- |
| `sentence_begin=true` | `InputBoundaryEvent.speech_started` |
| partial text | `InputBoundaryEvent.transcript_delta` |
| `sentence_end=true` 或 final transcript | `InputBoundaryEvent.speech_stopped(text)` |

### 回复生成

当 `VisionRealtimeAgentCore` 收到最终用户文本后：

1. 写入 user message。
2. 启动 `VisionResponseEngine.start_response()`。
3. `VisionResponseEngine` 请求视觉语言模型。
4. 每个 text delta 追加到 assistant buffer。
5. text delta 推入 TTS。
6. TTS audio chunk 进入 `RealtimeOutputController`。
7. pipeline 发出 `output_audio_delta`。
8. 视觉语言模型和 TTS 完成后，pipeline 发出 `output_finished`。

### 打断处理

Vision 链路中，打断由新的 `sentence_begin=true` 触发。

打断时必须：

1. 标记当前 generation interrupted。
2. 将当前 assistant buffer 写入消息历史，并追加 `<用户打断>`。
3. 取消当前 LLM 请求。
4. 取消当前 TTS 输出。
5. 取消当前 output stream。
6. 丢弃旧 generation 的后续 LLM/TTS 回调。

### 关闭连续对话

Vision 链路有两个关闭来源：

1. listening 状态空闲超时。
2. 视觉语言模型调用 `close_audio_session` Tool。

模型调用关闭 Tool 时，`VisionRealtimeAgentCore` 必须写入 assistant tool call message 和 tool result message，再发出 `PipelineEvent.close_requested`。

关闭时必须释放 ASR provider 和 TTS provider。

### Vision 内部时序

```plantuml
!include vision-realtime-server-side-sequence.puml
```

## OmniRealtimePipeline 设计

### 适用场景

`OmniRealtimePipeline` 用于原生实时音频模型链路。Omni provider 同时承担 VAD、ASR、LLM 和 TTS，服务器不能把 provider 原始事件直接暴露给外部调用方。

### 内部组件

| 组件 | 职责 |
| --- | --- |
| `OmniRealtimePipeline` | 实现 `RealtimeAgentRealtimePipeline` 统一接口，协调 Omni 专属组件和共享组件。 |
| `OmniAgentCore` | Omni 链路对话语义核心，负责上下文、消息历史、turn 状态、打断、关闭请求。 |
| `OmniInputBoundary` | 解释 Omni provider 的 speech 事件，把 provider 事件转换成统一输入边界事件。 |
| `OmniResponseEngine` | 管理 Omni provider session，解释 provider 音频输出和 transcript。 |
| `OmniProviderSession` | Omni 模型服务连接，内部包含 VAD、ASR、LLM、TTS 能力。 |

### 会话初始化

`open_session` 时，`OmniResponseEngine` 应创建并配置 Omni provider session。provider 原始配置只留在 `OmniResponseEngine` 内部，不进入外部契约。

### 上行连接绑定

`attach_upstream` 时，Omni 输入路径必须 ready。后续 `append_input_audio` 的音频帧持续送入 Omni provider。

### 下行连接绑定

`attach_downstream` 时，`OmniAgentCore` 绑定共享 `RealtimeOutputController`。Omni provider 返回的音频 delta 必须经过 `OmniResponseEngine` 转换为统一 `output_audio_delta`，再由外层写入下行连接。

### 输入边界解释

Omni 链路的用户语音边界来自 Omni provider：

| Omni provider 原始事件 | OmniInputBoundary 输出 |
| --- | --- |
| speech started | `InputBoundaryEvent.speech_started` |
| speech stopped | `InputBoundaryEvent.speech_stopped` |
| final transcript | 写入 user message |

provider 原始事件不能穿透到 `RealtimeAgentApp`。`RealtimeAgentApp` 只能看到统一的 `PipelineEvent.speech_started` 和 `PipelineEvent.speech_stopped`。

### 回复生成

Omni provider 直接返回助手音频。`OmniResponseEngine` 需要把 provider 音频 delta 转换为统一 `ResponseEvent.audio_delta`，再交给 `RealtimeOutputController`。

如果 provider 同时返回助手 transcript，`OmniAgentCore` 需要把 transcript 追加到 assistant buffer，用于消息历史和打断上下文。

### 打断处理

Omni 链路中，打断由 Omni provider 的 speech started 事件触发。

打断语义必须与 Vision 链路一致：

1. 标记当前 generation interrupted。
2. 将当前 assistant buffer 写入消息历史，并追加 `<用户打断>`。
3. 取消当前 Omni response。
4. 取消当前 output stream。
5. 丢弃旧 generation 的后续 provider 回调。

### 关闭连续对话

Omni 链路有两个关闭来源：

1. listening 状态空闲超时。
2. Omni provider 或 OmniAgentCore 判断用户请求结束连续对话。

关闭时必须关闭 Omni provider session，并释放输入、输出和内部状态。

### Omni 内部时序

```plantuml
!include omni-realtime-server-side-sequence.puml
```

## 两条链路的差异对比

| 维度 | VisionRealtimePipeline | OmniRealtimePipeline | 是否对外暴露差异 |
| --- | --- | --- | --- |
| VAD / 语音边界 | Paraformer 句子事件解释 | Omni provider speech 事件解释 | 否 |
| ASR | Paraformer final transcript | Omni provider transcript | 否 |
| LLM | Vision Model Provider | Omni provider 内置 | 否 |
| TTS | Streaming TTS Provider | Omni provider 内置 | 否 |
| output audio | TTS audio chunk | Omni audio delta | 否 |
| tool close | Vision 模型调用关闭 Tool | Omni 链路转换为关闭请求 | 否 |
| 打断标记 | `<用户打断>` 写入 assistant partial message | `<用户打断>` 写入 assistant partial message | 否 |
| 下行水位 | `RealtimeOutputController` | `RealtimeOutputController` | 否 |
| output finish | `output_finished` | `output_finished` | 否 |
| audio session close | `close_requested` | `close_requested` | 否 |

差异只存在于 pipeline 内部实现。外部调用方不能根据链路类型写分支处理。

## 状态模型

pipeline 内部至少需要维护以下状态：

| 状态 | 含义 |
| --- | --- |
| `initializing` | session 已打开，但上下行连接尚未 ready。 |
| `listening` | 正在持续接收上行音频，等待用户说话。 |
| `user_speaking` | 已判断用户开始说话，正在等待用户停止说话和最终输入。 |
| `thinking` | 已形成用户输入，正在请求模型或等待 provider 回复。 |
| `speaking` | 正在生成或发送助手输出。 |
| `closing` | 已请求关闭连续对话，正在释放输入、输出和 provider。 |
| `closed` | session 已关闭。 |

状态流转必须由 pipeline 内部集中管理，不能散落在 ASR、TTS、StreamService 或端侧逻辑里。

## Generation 语义

每次助手响应必须有一个单调递增的 `generation_id`。

`generation_id` 用于解决以下问题：

1. 用户打断后，旧模型请求仍然返回 delta。
2. TTS provider 在 cancel 后仍然回调音频。
3. Omni provider 在 response cancel 后仍然返回旧 response 事件。
4. 关闭 session 时，旧回调晚于关闭动作到达。

处理规则：

1. 当前 generation 被打断或关闭后，立即标记为 inactive。
2. 所有模型、TTS、Omni provider 回调都必须携带或绑定 generation id。
3. 回调到达时，如果 generation id 不是当前 active generation，则直接丢弃。
4. 丢弃旧回调不记录为 `response.failed`。
5. 丢弃旧回调不触发错误兜底语音。

## 配置项

设计需要以下配置语义。具体 YAML 路径可以在实现阶段按现有配置结构落地，但语义必须保持一致。

| 配置语义 | 作用 |
| --- | --- |
| ASR 句子等待时间 | Vision 链路传给 `paraformer-realtime-v2`，用于判断一句话结束。 |
| audio session 空闲超时 | listening 状态下无有效用户输入时，服务器主动关闭连续对话。 |
| 下行高水位阈值 | 端侧下行队列快满时通知服务器暂停发送。 |
| 下行低水位阈值 | 端侧下行队列回落后通知服务器继续发送。 |
| 输出 drain 超时 | 关闭连续对话时，等待当前输出完成的最大时长。 |
| provider connect timeout | ASR、TTS、Omni provider 建连超时。 |
| provider close timeout | 关闭 provider session 的最大等待时间。 |

## 实施边界

### 必须保留的现有组件

当前设计不是要求把现有系统全部推倒重写。以下组件仍应作为外部共享能力保留：

1. `RealtimeAgentApp`
2. `ControlService`
3. `StreamService`
4. `OutputService`
5. 现有 provider adapter
6. 现有 runs artifact 记录能力

`Realtime Audio Pipeline` 的目标是把 Vision / Omni 的实时音频对话语义收敛到同一层，而不是把 WebSocket、控制事件、stream 生命周期全部搬进 pipeline 内部。

### 不应该做的事情

1. 不在端侧实现连续对话阶段 VAD。
2. 不在 pipeline 热路径重复做连接身份校验。
3. 不让 Omni provider 原始事件穿透到 `RealtimeAgentApp`。
4. 不让 Vision 链路的 Paraformer 原始事件穿透到 `RealtimeAgentApp`。
5. 不用 `output.close` 表达一段输出结束。
6. 不把用户插话记录为异常失败。
7. 不在旧 generation 回调里播放错误兜底语音。

## 验收标准

设计落地后，至少需要满足以下验收点：

1. Vision 和 Omni 都通过同一组 `RealtimeAgentRealtimePipeline` 接口接入 `RealtimeAgentApp`。
2. 端侧只需要实现 [端侧音频交互标准.md](端侧音频交互标准.md)，不需要关心 Vision / Omni 差异。
3. Vision 链路上行连接建立时预热 ASR，下行连接建立时预热 TTS。
4. Omni 链路 provider 事件只在 `OmniInputBoundary` / `OmniResponseEngine` 内解释。
5. 用户插话时，两条链路都写入 assistant partial message，并追加 `<用户打断>`。
6. 旧 generation 回调被丢弃，不产生 `response.failed` 和错误兜底音频。
7. 一段输出结束使用 `output_finished` / `stream.output.finish` 语义，不关闭下行长连接。
8. 连续对话关闭时，Vision 释放 ASR 和 TTS，Omni 释放 Omni provider session。
9. 空闲超时和模型关闭请求都能触发 `close_requested`。
10. 下行水位控制对 Vision 和 Omni 使用同一套 pause / resume 接口。

