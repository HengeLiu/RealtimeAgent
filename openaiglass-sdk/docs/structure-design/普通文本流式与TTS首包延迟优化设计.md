# 普通文本流式与 TTS 首包延迟优化设计

更新时间：2026-04-30
对应版本：sdk-v75

## 1. 设计目标

普通语音问答的体验不应等待模型完整回复后才开始合成语音。SDK 需要把模型普通文本增量直接透传给流式 TTS，让首个可播音频尽早进入播放队列。

本轮目标：

1. 普通文本回复可以通过 `reply_text_delta_callback` 进入 `VoiceRuntime`。
2. `VoiceRuntime` 在首个文本增量到达时立即创建回复播放流和 TTS 会话。
3. 首个 TTS 音频分片到达后立即下发 `actuator.audio.play`。
4. 运行态快照能观察首文本、首音频和首播放请求时间，便于后续回归首包延迟。

本轮不处理：

1. 全双工实时语音。
2. 用户语音打断。
3. 播放抢占和通知仲裁。
4. 具体 TTS 服务商的底层 WebSocket 性能优化。

## 2. 链路边界

普通文本流式链路分为三段：

1. AgentCore 从 `Runner.run_streamed(...).stream_events()` 读取模型文本增量。
2. AgentCore 通过 `reply_text_delta_callback(text_delta)` 透传给 `VoiceRuntime`。
3. `VoiceRuntime` 将文本增量推入 `StreamingTtsSession`，TTS 音频分片通过播放队列下发给眼镜。

当前改造覆盖普通 Agents SDK 文本 delta、图片解读 Chat Completions 文本 delta，以及音频原生 Chat Completions 最终回复文本 delta。工具调用分片只用于组装工具调用参数，不会被当成最终回复文本。

## 3. AgentCore 文本增量提取

Agents SDK 的流式事件中，普通文本通常来自 `raw_response_event`：

```text
event.type = raw_response_event
event.data.type = response.output_text.delta
event.data.delta = 文本增量
```

SDK 新增普通文本增量提取逻辑：

1. 识别对象和字典两种事件结构。
2. 优先提取 `response.output_text.delta`。
3. 兼容 `choices[].delta.content` 形式。
4. 只处理文本增量，不把工具调用参数或工具结果当成最终回复。

如果流式事件已经产生文本增量，最终 `AgentTurnResult.reply_text` 优先使用增量拼接结果；如果没有增量，再回退到 `run_result.final_output`。

`sdk-v74` 起，音频原生 Chat Completions 链路也使用 `stream=True`。该链路会：

1. 从 `choices[].delta.content` 提取最终回复文本增量，并立即调用 `reply_text_delta_callback`。
2. 从 `choices[].delta.tool_calls` 累积工具调用分片，流结束后统一执行 Tool。
3. 工具结果回填后继续以流式方式请求下一轮模型输出，直到得到最终文本或超过工具循环上限。

## 4. VoiceRuntime 首包观测

`PlaybackStreamContext` 新增以下字段：

| 字段 | 含义 |
| --- | --- |
| `first_text_delta_at_ms` | 当前回复播放流收到首个文本增量的时间。 |
| `first_audio_chunk_at_ms` | 当前回复播放流收到首个可播放 TTS 音频分片的时间。 |
| `first_play_request_at_ms` | 当前回复播放流首次下发 `actuator.audio.play` 的时间。 |

运行态快照新增以下字段：

| 字段 | 含义 |
| --- | --- |
| `reply_first_text_delta_at_ms` | 当前回复首文本增量时间。 |
| `reply_first_audio_chunk_at_ms` | 当前回复首音频分片时间。 |
| `reply_first_play_request_at_ms` | 当前回复首播放请求时间。 |
| `reply_text_to_first_audio_ms` | 首文本到首音频的耗时。 |
| `reply_audio_to_play_request_ms` | 首音频到播放请求的耗时。 |

这些字段用于观测 SDK 链路是否真的在流式推进，不代表模型、TTS 服务或网络的长期性能承诺。

## 5. 失败与回退

当前 TTS 会话仍保留两种实现：

1. 支持真正增量合成时，`StreamingTtsSession.push_text(...)` 会持续把文本片段推给 TTS。
2. 不支持真正增量合成时，`BufferedStreamingTtsSession` 会缓存文本并在 `finish()` 时生成完整音频。

因此，本轮可以保证“普通文本 delta 能进入 TTS 调度层”，但如果运行环境回退到全文 TTS，首音频仍然会等到 `finish()` 后才出现。运行态快照中的首文本和首音频时间差可以直接暴露这种情况。

## 6. 工具调用前置播报

### 6.1 调研结论

OpenAI Realtime / Responses API 都支持工具调用和流式事件。`agent_tts` 链路默认不把“模型先输出一段普通文本，再稳定返回工具调用”作为唯一交互契约，因为工具调用本身是模型响应中的决策事件；如果强行要求模型先说等待语，再调用工具，会引入模型行为不稳定、重复播报和与最终回答倒序的问题。

因此 SDK 默认采用框架级前置播报：

1. 模型仍只负责理解用户意图和选择 Tool。
2. `ToolGateway` 在 Tool 真正执行前读取 `ToolSpec.progress_message`。
3. `AgentToolContext.progress_callback` 把短文本交给 `VoiceRuntime` 的中间播报通道。
4. `VoiceRuntime` 同步注册中间播报播放流，再异步执行 TTS 合成。
5. `progress_message` 支持单句或多句候选；多句候选在工具调用前随机选择一条。
6. 同一轮同一工具只播报一次，避免工具循环或重试导致重复提示。

`sdk-v80` 起，框架级前置播报由模型首输出类型自动判定：首输出是工具调用时，SDK 触发 `ToolSpec.progress_message`；首输出是文本或音频时，SDK 不再额外插入等待提示。Omni Realtime 音频直出链路允许模型已经播出的语音继续播放，随后由 SDK 执行工具、回填结果并继续接收最终 `response.audio.delta`。

`VoiceRuntime` 需要特别处理最终回复 TTS 预热与前置播报的顺序：TTS 会话可以在 Agent 请求前预热，但最终回复播放流不能提前注册到播放仲裁器。否则尚未产生任何最终回复音频的预热流会占住 active playback，让前置播报只能排队到最终回复之后。当前实现是在最终回复首段文本到达时才创建最终回复播放流。

### 6.2 前置播报音频来源

`progress_message` 是静态短文本，但不同产品对首包延迟和音色一致性的优先级不同。`TOOL_PROGRESS_AUDIO_MODE` 用于控制工具前置播报音频来源：

1. `cached`：SDK 在 `VoiceRuntime` 初始化后异步执行一次缓存预加载，工具调用时读取本地 PCM。
2. `realtime`：SDK 不预加载缓存，工具调用前把选中的固定提示文本发送给当前 TTS 服务，边生成边写入同一条下行播放流。

`cached` 模式的缓存流程：

1. 从 `ToolRegistry.list_progress_messages()` 读取当前所有工具的播报文案；如果某个工具配置了多句候选，会展开为多条文案。
2. 按 `tts_model_name`、`tts_voice`、`tts_sample_rate_hz`、目标播放采样率和文本生成稳定 hash。
3. 缓存文件写入 `VOICE_RUNS_ROOT/progress-audio-cache/<hash>.wav`。
4. 如果文件已存在且是 16k 单声道 16bit PCM WAV，直接读取 PCM 到内存。
5. 如果文件不存在，调用当前配置的 TTS 生成一次音频，重采样成 16k PCM 后写入 WAV。
6. 工具调用时先随机选择本次播报文案，再优先读取内存缓存，命中后仍通过统一播放流写出；未命中或预加载失败时回退实时 TTS。

两种模式只影响工具前置播报，不影响最终回复 TTS 或 Omni Realtime 音频直出。缓存目录位于 `runs/` 下，属于运行产物，不应提交。

### 6.3 适用范围

这项能力只覆盖“模型已经决定调用工具，但工具执行需要等待”的静默段，例如：

1. 查询设备或后台任务状态。
2. 触发真实抓拍。
3. 建立手机视频直连任务。
4. 业务 Tool 声明的其他耗时动作。

它不解决模型首轮决策前的静默等待。模型首轮决策前的延迟仍应通过 ASR 前移、Agent 资源预热、工具面收敛、模型选择和 Realtime 直出链路继续优化。

### 6.4 业务扩展面

公开 SDK `BaseTool` 新增可选属性：

```python
progress_message = [
    "我先处理一下，请稍等。",
    "稍等，我看一下。",
    "好，我来处理。",
]
```

业务 Tool 不应直接调用播放器，也不应自行写控制消息。前置播报属于 SDK 语音运行时职责，业务只声明适合该 Tool 的简短提示即可；为了避免重复感，建议配置 3 到 5 句候选。

## 7. 验收口径

自动化验收覆盖：

1. 普通 `raw_response_event` 文本增量会进入 `reply_text_delta_callback`。
2. 最终回复文本优先由流式文本增量拼接得到。
3. `VoiceRuntime` 快照会记录首文本、首音频和首播放请求时间。
4. 首文本到首音频、首音频到播放请求的延迟字段可读取。
5. 带 `progress_message` 的 Tool 在执行前会触发一次进度播报。
6. 公开 SDK `BaseTool.progress_message` 会透传到 agent-core 的 `ToolSpec`。

真机联调观察点：

1. 普通问答时，服务端日志应先出现 Agent 文本增量，再出现 TTS 音频分片。
2. `/api/runtime/devices` 或运行态快照中能看到 `reply_text_to_first_audio_ms`。
3. 如果 `reply_text_to_first_audio_ms` 很大，应先确认当前 TTS 是否回退到了全文合成。
4. 耗时 Tool 调用时，最终回答前可先听到 `progress_message` 对应的短播报；服务端日志应出现对应 `tool.call` 和中间播报播放流。
