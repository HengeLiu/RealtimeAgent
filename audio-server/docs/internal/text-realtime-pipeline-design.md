# Text 链路实时语音化设计

## 背景

当前仓库同时支持两条语音交互链路：

- Omni / realtime 链路：`sensor.mic -> Omni Realtime provider -> 原生 audio delta -> actuator.speaker`
- Text 链路：`sensor.mic -> ASR -> text LLM -> streaming TTS -> actuator.speaker`

Omni 链路把 ASR、turn detection、LLM、TTS 和工具调用时机交给 provider。Text 链路使用普通文本模型，必须由 SDK 自己编排 turn、工具调用、TTS、播放和打断。

公开方案调研结论：

- LiveKit Agents 把非 realtime model 语音助手建模为 `STT -> LLM -> TTS` pipeline，并提供 `stt_node()`、`llm_node()`、`tts_node()` 等节点用于插入自定义逻辑。参考：<https://docs.livekit.io/agents/logic/nodes/>
- LiveKit 的 turn detection 把 VAD、STT endpointing、turn detector model、manual turn control 分开处理，并把 interruption 作为一等能力。参考：<https://docs.livekit.io/agents/logic/turns/>
- Pipecat 也采用可组合 pipeline，强调 VAD 只负责 speech/silence，真正 turn end 需要 Smart Turn 或 speech timeout。参考：<https://docs.pipecat.ai/pipecat/learn/speech-input>
- 2026 年 realtime voice agent 教程仍把 cascaded streaming pipeline 作为自托管 voice agent 的实用架构：streaming STT、支持 function calling 的 streaming LLM、streaming TTS。参考：<https://arxiv.org/abs/2603.05413>

因此 Text 链路不应该模仿 Omni provider 内部黑盒，而应该做成可观测、可控的级联实时管线。

## 真正实时的 Text 语音链路定义

Text 链路的“实时”不能只表示服务端使用了 streaming API，而是每一段上游增量都要尽快推动下游工作：

1. 端侧麦克风第一段 `sensor.mic` chunk 到达后，ASR 就开始流式识别，而不是等待整段录音结束。
2. 端侧语音结束时，ASR 应该已经处理完大部分音频，`input_transcript.done` 应尽量与音频结束接近同时出现。
3. Text model 发出第一个 `text_delta` 后，SDK 立即把这段文本送入 TTS，不等待自然停顿、长度阈值、完整 assistant 回复或是否会出现后续 tool call。
4. TTS 产生第一段音频 chunk 后，OutputService 立即通过 `actuator.speaker` 数据流下发给端侧，不等待整段 TTS 合成完成。
5. 最终 assistant 消息按已经释放给用户的 delta 顺序写入；如果后续出现 tool call，工具调用前已经播出的自然语言提示必须保留。
6. `server.py` 虽然是 Omni / Text 共用的 WebSocket 传输层，但它不能让上行 chunk 的同步处理占住 aiohttp event loop；所有可能持续处理或触发下游同步工作的大量 stream chunk，都必须通过线程执行或其他非阻塞方式让出事件循环，保证控制事件和下行音频发送协程能及时运行。

从运行产物观察时，理想时间线应该是：`input_transcript.delta* -> input_transcript.done -> model.request -> text.response_gate.buffered -> text.response_gate.released(reason=text_delta_realtime) -> assistant_text.delta -> assistant_audio.delta* -> stream.output.summary`。其中 `text.response_gate.buffered` 与 `released` 不应该因为等待标点或完整回复而拉开明显间隔。

浏览器端“打开 speaker stream”和“听到第一段音频”之间允许存在 TTS 首包延迟。真实首响拆解必须按三个边界判断：

- `stream.output.open.requested -> stream.output.first_chunk.enqueued`：主要反映 TTS provider 首音频生成延迟。
- `stream.output.first_chunk.enqueued -> stream.output.first_chunk.sent`：主要反映服务端下行队列、aiohttp event loop 调度和 WebSocket backpressure；正常情况下 `queue_wait_ms` 应接近 0。
- `stream.output.first_chunk.sent -> browser playback first audio chunk`：主要反映端侧 WebSocket 接收、解码和播放调度；正常情况下应在同毫秒到几十毫秒内。

如果 `queue_wait_ms` 升高到秒级，说明问题不在 TTS provider，也不在浏览器播放，而是服务端事件循环或 WebSocket 下行发送被阻塞。2026-05-16 的真实浏览器回放曾观察到 `queue_wait_ms=4903`，根因是 stream WebSocket 接收循环同步处理大量 mic chunk 饿住同一 aiohttp event loop；修复后同场景降为 `queue_wait_ms=0`。

## 官方 API 核对

### DashScope 实时 ASR

官方文档：<https://www.alibabacloud.com/help/zh/model-studio/real-time-speech-recognition>

确认点：

- 官方实时识别目标是“边说边出文字”，Fun-ASR Python 示例使用 `Recognition(model='fun-asr-realtime', format='pcm', sample_rate=16000, semantic_punctuation_enabled=False, callback=callback)`。
- 官方示例流程是：创建 `Recognition`，调用 `recognition.start()`，麦克风循环读取 16k 单声道 PCM，每次读取后调用 `recognition.send_audio_frame(data)`，结束时调用 `recognition.stop()`。
- 识别结果通过 `RecognitionCallback.on_event()` 返回，`RecognitionResult.is_sentence_end(sentence)` 用于区分中间结果和句子结束结果。

当前实现结论：

- `DashScopeAsrProviderAdapter` 使用 `dashscope.audio.asr.Recognition`、`format="pcm"`、`sample_rate=16000`、`semantic_punctuation_enabled=False`，第一段 `sensor.mic` chunk 到达时创建 provider 并启动识别，会对后续音频 chunk 持续调用 `send_audio_frame()`；这符合官方实时 ASR 使用方式。
- 当前每条 `sensor.mic` stream 独立创建 ASR provider，final chunk 到达后调用 `stop()` 并短等待回调收尾，符合官方“开始、持续送帧、结束 stop”的会话生命周期。
- 配置 `asr_model="fun-asr-realtime"` 是实时接口模型；不要把非实时文件识别模型替换到这条链路。

### DashScope 实时 TTS

官方文档：<https://help.aliyun.com/zh/model-studio/cosyvoice-python-sdk>

确认点：

- `SpeechSynthesizer` 有三类调用方式：非流式 `call()`、单向流式 `call()` 加 `ResultCallback.on_data()`、双向流式 `streaming_call()` / `streaming_complete()`。
- 双向流式场景允许多次调用 `streaming_call()` 按顺序提交文本片段，服务端通过 `ResultCallback.on_data()` 实时返回音频；最后必须调用 `streaming_complete()`，否则结尾文本可能无法成功合成为语音。
- 官方文档明确说明：流式输入时服务端会自动分句，完整语句立即合成，不完整语句会缓存到完整后再合成；`streaming_complete()` 会强制合成所有已接收但未处理的文本片段，包括未完成句子。
- `AudioFormat.PCM_24000HZ_MONO_16BIT` 属于官方支持的 PCM 输出格式之一，当前端侧 speaker 配置 24k PCM 可以直接对应。

当前实现结论：

- `DashScopeStreamingTTS` 使用 `dashscope.audio.tts_v2.SpeechSynthesizer`、`streaming_call(text)`、`ResultCallback.on_data()` 和最终 `streaming_complete()`，符合官方双向流式 TTS 用法。
- `OutputService` 的后台 drain pump 会在两次 text delta 之间持续读取 `on_data()` 产生的音频并下发端侧，因此 SDK 侧没有等待完整 TTS 结束才播放。
- 需要注意 provider 限制：即使 SDK 已经把第一个 text delta 立刻送入 `streaming_call()`，CosyVoice 服务端也可能因为文本片段不是完整语句而缓存，不立即返回首个音频 chunk。运行产物应分别观察 `assistant_text.delta`、`assistant_audio.delta` 和 `tts_first_audio_latency_ms`，不能只看 Text gate 是否释放。

## 关键时间点观测

为了定位“模型文字已经全部打印但端侧还没声音”的真实瓶颈，Text 链路会在终端和 `agent-events.jsonl` / `model-events.jsonl` 中记录以下时间点：

| 事件 | 含义 |
| --- | --- |
| `text.timeline.audio.first_chunk_received` | 服务端收到第一段 `sensor.mic` 音频 chunk。 |
| `text.timeline.audio.input_done` | 服务端收到麦克风输入 final chunk，即本轮音频输入结束。 |
| `text.timeline.asr.first_char` | ASR 首次返回非空文本。 |
| `text.timeline.asr.done` | ASR 返回最终文本。 |
| `text.timeline.llm.first_token` | Text model 首次返回非空文本 delta。 |
| `text.timeline.llm.done` | Text model 本轮最终文本 delta 全部返回。 |
| `text.timeline.tts.first_audio_chunk` | Streaming TTS 首次产生可下发的音频 payload。 |
| `text.timeline.tts.audio_done` | Streaming TTS 本轮音频全部 drain 完成。 |
| `text.timeline.summary` | 以上时间点相对首个音频 chunk 的毫秒级汇总。 |
| `stream.output.first_chunk.enqueued` | 服务端 speaker 音频首个 chunk 写入下行 stream 队列。 |
| `stream.output.first_chunk.sent` | 服务端首个 speaker 音频 chunk 的 `ws.send_bytes()` 已返回，包含 `queue_wait_ms`。 |
| `stream.output.send.summary` | 服务端 output stream 下行发送汇总，包含入队 / 发送 chunk 数、bytes 和首尾发送耗时。 |
| `playback first audio chunk` | 浏览器眼镜收到首个 speaker 音频 chunk，包含 `since_open_ms`。 |
| `playback scheduled` | 浏览器已把首个音频 chunk 放入 WebAudio 调度，包含 `scheduled_delay_ms`。 |

这些事件都带 `since_audio_start_ms` 和 `since_previous_checkpoint_ms`。排查时优先看 `text.timeline.summary`：

- `llm.first_token` 已出现但 `tts.first_audio_chunk` 很晚，说明瓶颈在 TTS provider 生成首包或 SDK drain。
- `tts.first_audio_chunk` 已出现但端侧仍听不到，继续看 `stream.output.first_chunk.enqueued`、`stream.output.first_chunk.sent`、`queue_wait_ms` 和端侧 `playback first audio chunk`。
- `llm.done` 早于 `tts.first_audio_chunk`，说明 TTS 没有在模型输出过程中返回首包，常见原因是 provider 对不完整语句做了缓存。
- `stream.output.first_chunk.enqueued` 很早但 `stream.output.first_chunk.sent` 很晚，说明服务端 event loop 或 WebSocket 下行发送被阻塞；不能把这类问题误判成 TTS 慢。
- `stream.output.first_chunk.sent` 与端侧 `playback first audio chunk` 基本同时出现，但 `since_open_ms` 较大，说明 output stream 打开早于 TTS 首包，真实空等发生在 TTS 首音频生成阶段。

## 当前链路

```plantuml
@startuml
title 当前 Text 链路

participant "端侧 sensor.mic" as Mic
participant "AudioPipeline" as Audio
participant "AsrPipeline" as ASR
participant "TextAgentCore" as Text
participant "ContextCompiler" as Ctx
participant "Text Model" as LLM
participant "ToolGateway" as Tool
participant "OutputService" as Out
participant "Streaming TTS" as TTS
participant "端侧 speaker" as Speaker

Mic -> Audio: PCM chunk
Audio -> Text: append_audio_event(chunk)
Text -> ASR: append_audio(chunk)
ASR --> Text: final transcript
Text -> Ctx: compile(prompt, memory, history, tools)
Text -> LLM: stream_messages(messages, tools)
LLM --> Text: text delta 或 tool_call
Text -> Tool: call_sync_safe()
Tool --> Text: ToolResult
Text -> LLM: tool result follow-up
LLM --> Text: final text delta
Text -> Out: assistant_text.delta
Out -> TTS: synthesize_delta()
Out -> Speaker: actuator.speaker chunk
@enduml
```

## 与 Omni 链路的差异

| 维度 | Omni / realtime | Text |
| --- | --- | --- |
| Turn boundary | provider 内置 VAD / semantic VAD | 当前依赖 ASR final |
| 模型输入 | PCM 音频流和工具 schema | transcript、messages、工具 schema |
| 输出 | provider 原生 PCM audio delta | 文本 delta 经 TTS |
| 工具调用 | provider event 驱动，结果回填 provider | SDK tool loop 驱动，结果回填 text model |
| 可控性 | 工具前/工具中音频较难硬拦截 | 可以在文字进入 TTS 前做代码级门控 |
| 风险面 | provider 行为黑盒 | pipeline 编排复杂度在 SDK |

## 目标架构

```plantuml
@startuml
title 目标 Text 实时语音链路

participant "AudioPipeline" as Audio
participant "TextTurnController" as Turn
participant "ASR Adapter" as ASR
participant "TextResponseController" as Resp
participant "ContextCompiler" as Ctx
participant "TextResponseGate" as Gate
participant "ToolGateway" as Tool
participant "TextSpeechController" as Speech
participant "OutputService" as Out

Audio -> Turn: normalized PCM
Turn -> ASR: streaming audio
ASR --> Turn: partial / final transcript
Turn -> Resp: user turn completed
Resp -> Ctx: compile context
Resp -> Gate: LLM text delta / tool_call
Gate -> Tool: tool_call accepted
Tool --> Resp: ToolResult
Resp -> Gate: final response delta
Gate -> Speech: safe text chunks
Speech -> Out: assistant_text.delta / final
@enduml
```

## 核心约束

Text 链路不能把 “tool_call 之前出现的文本” 简单判定为模型废话。普通文本模型经常会先给用户一句自然提示，例如“我先看一下”或“稍等，我查一下”，然后再发起工具调用。这类内容属于用户可听响应，必须满足：

- 按模型 delta 原顺序进入 TTS。
- 写入本轮 assistant 消息。
- 作为是否需要系统 progress audio 的判断依据。
- 不能因为后续出现 tool_call 而被丢弃。

系统 `Tool.progress_message` 只在模型首输出就是 tool_call、且本轮尚未释放任何模型文本时插入，避免用户听到两段重复的“稍等/正在处理”。

## 分阶段实施

### Phase 1：Text 私有输出门控

状态：已完成。

目标：先把 text model 的早期输出纳入代码级门控，避免工具调用、进度音和 TTS 播放时机互相打架，同时保留工具调用前的自然语言提示。

范围：

- 只改 `TextAgentCore` 私有逻辑。
- 不改 Omni provider adapter。
- 不改 `AudioPipeline`、`ToolGateway`、`OutputService` 的共享语义。

策略：

1. 每次 LLM streaming 调用内部新增 Text response gate。
2. 普通文本 delta 先进入 gate，由 gate 判断何时安全释放给 TTS。
3. 如果本次模型调用最终没有 tool call，则所有未释放文本按原始 delta 顺序释放给 TTS。
4. 如果本次模型调用出现 tool call，也先释放已缓冲文本；这段文本可能是模型对用户的合理提示，不能默认当成废话丢弃。
5. 只有当模型首输出就是 tool call、没有任何已播文本时，才由系统的 `Tool.progress_message` 负责工具前置播报。
6. 记录 `text.response_gate.buffered/released/discarded`，方便从 runs 中确认门控行为；`discarded` 仅保留给后续更细粒度策略使用。

### Phase 2：Turn 状态机

状态：已完成服务端最小状态机。

目标：显式记录 Text 链路状态，支撑后续 partial ASR、打断和延迟分析。

已落地状态：

- `listening`
- `transcribing`
- `thinking`
- `tool_running`
- `speaking`
- `interrupted`
- `completed`
- `failed`

状态变化通过统一事件 `agent.turn_state.changed` 写入 `agent-events.jsonl` / `model-events.jsonl`，并同步进入 `AgentEventBuffer`。Text 和 Realtime / Omni 共用事件名和状态枚举，通过 payload 中的 `agent_core`、`modality`、`provider`、`provider_event` 区分来源。当前阶段先做服务端状态观测，不改变 ASR provider 的 final transcript 语义。

### Phase 3：逐 delta TTS 与首音频下发

状态：已完成首版逐 delta 实时释放。

目标：在不牺牲工具前门控的前提下，把 LLM 首个 text delta 尽快送入 TTS，并让 TTS 首段音频尽快下发端侧。

策略：

- 每个非空 text delta 到达后都立即释放给 OutputService/TTS，释放原因记录为 `text_delta_realtime`。
- 不再等待中文/英文自然停顿标点，也不再等待完整 assistant 回复。
- tool_call 到来时立即释放已缓冲文本，保证“工具前自然提示”不丢失。
- TTS 侧依赖 streaming source 和后台 drain pump 尽快拉取音频；如果 provider 本身首包慢，运行产物应通过 `assistant_audio.delta` 与 `stream.output.summary` 的时间差暴露。
- 所有释放事件记录 `reason`，例如 `text_delta_realtime` 或 `explicit_release`。
- Output stream 可以在首个文本 delta 释放时提前打开，但真实首响不能用 `stream.output.open.requested` 衡量；必须以 `stream.output.first_chunk.enqueued`、`stream.output.first_chunk.sent` 和端侧 `playback first audio chunk` 判定。
- `AudioChatHttpServer.stream_ws()` 不能在 aiohttp event loop 内同步执行大量 `write_input_chunk()`；非 final stream chunk 使用 `asyncio.to_thread()` 顺序处理，final mic chunk 使用后台 task 处理，避免 ASR/LLM/Tool 或大量 mic chunk 阻塞控制事件与下行音频发送。

当前还没有引入跨 Text/TTS 的独立背压队列，也没有改变 `OutputService` 的播放仲裁策略；本阶段先保证 Text gate 不再成为首音频延迟来源。

### Phase 4：打断和已播历史

状态：已完成服务端可测试语义；真机体验仍需人工验收。

目标：用户说话打断时，取消 LLM、TTS 和 output stream，并记录用户实际听到的 assistant 片段。

已落地语义：

- `TextAgentCore.interrupt()` 会取消 ASR、text model 和当前用户输出流。
- 如果打断发生在 Text 模型仍在 streaming 的过程中，工具循环只返回已经释放给 TTS 的文本。
- 最终 assistant 消息带 `interrupted=true` 和 `interrupted_reason`。
- runs 中记录 `response.interrupted`、`agent.response.cancelled` 和 `agent.turn_state.changed(state=interrupted)`。

真实麦克风 speech-start 触发质量、false interruption 过滤和用户实际听感，需要通过端侧 VAD / 唤醒策略继续人工验收；本阶段先保证 server 在收到 `control.user.interrupt.detected` 后具备正确收口语义。

## Phase 1 验收

必须覆盖：

- 普通文本回答仍能输出并保存 assistant 消息。
- 首输出就是 tool call 时仍会播放系统 progress audio。
- 先输出文本再 tool call 时，工具前文本正常进入 TTS 和 assistant 最终消息，且不重复插入系统 progress audio。
- 工具结果后的最终回答正常进入 TTS 和消息历史。
- Realtime / Omni 相关测试不因 Text 私有改动失败。

## 当前实现摘要

```plantuml
@startuml
title 当前已落地 Text 工具调用与播报顺序

participant "Text Model" as LLM
participant "TextResponseGate" as Gate
participant "ToolGateway" as Tool
participant "OutputService/TTS" as Out
database "messages.jsonl" as Msg
database "agent-events.jsonl" as Events

LLM -> Gate: text delta
Gate -> Events: text.response_gate.buffered
Gate -> Out: immediate delta release
Gate -> Events: text.response_gate.released
LLM -> Gate: tool_call
Gate -> Out: release remaining text before tool
Gate -> Tool: call tool
Tool --> Gate: ToolResult
Gate -> LLM: tool result message
LLM -> Gate: final answer delta
Gate -> Out: release final answer
Gate -> Msg: assistant_text.done
@enduml
```

实现文件：

- `audio-server/audio_chat/agent_core/text.py`
  - `TextResponseGate`：缓冲、逐 delta 实时释放、显式释放和 discard 观测事件。
  - `TextAgentCore._set_turn_state()`：Text turn 状态机事件。
  - `TextAgentCore.interrupt()`：服务端打断收口和当前输出取消。
- `audio-server/audio_chat/server.py`
  - 下行 speaker stream 记录 `stream.output.first_chunk.enqueued`、`stream.output.first_chunk.sent(queue_wait_ms)` 和 `stream.output.send.summary`。
  - stream WebSocket 接收循环把非 final stream chunk 放到 worker thread 顺序处理，避免同步 `write_input_chunk()` 饿住 aiohttp event loop。
- `examples/dev-support/devices/browser-glass/index.html`
  - 浏览器日志使用毫秒级时间戳。
  - 只记录 speaker 首包、调度、drain 汇总，不再逐 chunk 打印音频接收日志。
- `audio-server/tests/test_progress_audio.py`
  - 覆盖首输出 tool_call 的 progress audio。
  - 覆盖先文本再 tool_call 时保留工具前文本且不重复播 progress audio。
- `audio-server/tests/test_agent_core_router.py`
  - 覆盖逐 delta 实时释放，包括无标点首个 delta 立即释放。
  - 覆盖 Text 状态机事件。
  - 覆盖打断后只保存已释放 assistant 文本。

## 验证记录

- `uv run python -m pytest audio-server/tests/test_progress_audio.py audio-server/tests/test_agent_core_router.py -k 'text_agent or progress_audio' -q`
  - 结果：通过，15 passed。
- `uv run python -m pytest audio-server/tests/test_realtime_audio_agent_core.py audio-server/tests/test_context_compiler.py audio-server/tests/test_voice_session_modes.py -q`
  - 结果：通过，29 passed。
- `uv run python -m audio_chat_python_playback_glass run --server-url http://127.0.0.1:18765 --suite examples/dev-support/devices/python-playback-glass/suites/smoke.yaml --runs-root /tmp/audio-chat-text-e2e/runs --report /tmp/audio-chat-text-e2e/reports/smoke/report.json`
  - 配置：临时 `/tmp/audio-chat-text-e2e/server.yaml`，`agent.mode=text`，ASR/Text/TTS 均为 mock provider，复用 for-blind capabilities，清空临时 denylist 以允许 `capture_photo`。
  - 结果：通过，2 cases passed。
  - 普通问答 case：`messages.jsonl` 记录 `你是谁呀 -> 我是 audio-chat 文本链路助手。`，产生 speaker WAV。
  - 普通问答 case：`agent-events.jsonl` 记录每个 `text.response_gate.buffered` 后立即出现 `assistant_text.delta` 和 `text.response_gate.released(reason=text_delta_realtime)`；`stream-events.jsonl` 记录 mock TTS `tts_first_audio_latency_ms=2`。
  - 看图工具 case：`tool-events.jsonl` 记录 `capture_photo ok=true`，`assets.jsonl` 记录 `asset.stored sensor.rgb`，`agent-events.jsonl` 记录 `tool_call.delta`、`tool.progress_message.emitted`、`agent.turn_state.changed(state=tool_running)` 和逐 delta `text.response_gate.released(reason=text_delta_realtime)`。
  - 时间线 case：`agent-events.jsonl` 记录 `text.timeline.summary`，mock 链路中 `tts.first_audio_chunk` 早于或接近 `llm.done`，可证明当前观测能区分 LLM 输出和 TTS 首音频返回。
- 2026-05-16 真实浏览器眼镜 + DashScope Text 链路回放：
  - 修复前：`stream.output.first_chunk.enqueued=13:02:55.683`，`stream.output.first_chunk.sent=13:03:00.586`，`queue_wait_ms=4903`；浏览器同到 `13:03:00.588` 才收到首包，证明瓶颈在服务端 event loop / 下行发送，而不是浏览器播放。
  - 修复后：`stream.output.first_chunk.enqueued=13:22:09.270`，`stream.output.first_chunk.sent=13:22:09.271`，`queue_wait_ms=0`；浏览器 `playback first audio chunk=13:22:09.271`，证明服务端下行和端侧接收已恢复实时。
  - 修复后仍观察到 `since_open_ms≈590`，原因是 output stream 在 `13:22:08.681` 先打开，而 TTS 首音频到 `13:22:09.270` 才生成；这属于 DashScope TTS 首包延迟，不属于 server/browser 传输延迟。

待补充：

- 真实 DashScope ASR/Text/TTS provider 和真机麦克风/扬声器体验未在本轮验证；本轮是 mock provider + 真实 WebSocket 端侧协议验证。
- `examples/dev-support/tests/playback/test_python_playback.py` 仍有旧 in-process playback 失败：`run_playback()` 兼容占位没有输出 chunk，且测试引用了不存在的 `testdata/audio-sample/wav/看一下我前面有什么.wav`。这不影响本轮使用的网络无头端侧 `python-playback-glass` 结果，但需要后续单独清理旧测试。

## 真实回放发现并修复的问题

1. server 在同一条 stream WebSocket handler 中同步等待 final mic chunk 处理完成，Text 工具等待端侧图片时会阻塞同一连接继续读取 `sensor.rgb` 二进制帧。
   - 修复：`audio_chat.server.AudioChatHttpServer.stream_ws()` 对 `sensor.mic final` 使用后台 `asyncio.to_thread()` 分发，不阻塞后续 WebSocket 输入。
2. `python-playback-glass` 在发送图片二进制帧后立即发送 `stream.input.closed`，控制 WebSocket close 事件可能先于 stream 二进制帧被 server 处理，导致图片 chunk 被视为 late chunk。
   - 修复：图片 chunk 发送后短暂等待，再发送 close；runner 对带 sensor fixture 的 case 不再把工具 progress audio 误判为最终输出。
3. `python-playback-glass` 原断言只检查工具被调用，不能发现工具 timeout。
   - 修复：新增 `tools.succeeded` 断言，并在 `look_front.yaml` 要求 `capture_photo` 成功。
