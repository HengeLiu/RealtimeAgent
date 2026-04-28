# 普通文本流式与 TTS 首包延迟优化设计

更新时间：2026-04-28
对应版本：sdk-v7

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

本轮改造只处理普通文本 delta 透传和首包观测。图片解读主链路原本已经能把 Chat Completions 的文本 delta 透传给 TTS，本轮保持兼容。

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

## 6. 验收口径

自动化验收覆盖：

1. 普通 `raw_response_event` 文本增量会进入 `reply_text_delta_callback`。
2. 最终回复文本优先由流式文本增量拼接得到。
3. `VoiceRuntime` 快照会记录首文本、首音频和首播放请求时间。
4. 首文本到首音频、首音频到播放请求的延迟字段可读取。

真机联调观察点：

1. 普通问答时，服务端日志应先出现 Agent 文本增量，再出现 TTS 音频分片。
2. `/api/runtime/devices` 或运行态快照中能看到 `reply_text_to_first_audio_ms`。
3. 如果 `reply_text_to_first_audio_ms` 很大，应先确认当前 TTS 是否回退到了全文合成。
