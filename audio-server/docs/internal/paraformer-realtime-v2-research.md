# Paraformer Realtime V2 调研与接入实验

本文记录 `paraformer-realtime-v2` 在当前 realtime-agent 项目中的接入判断。目标不是立刻改主链路，而是先回答几个关键问题：

- 它是否能替代当前 Vision Realtime 链路里分散的 ASR、VAD、标点预测和语气词过滤能力。
- 服务端能否从模型回调中知道“用户开始说话”和“用户结束说话”。
- 如果能接入，应该接到哪一层，哪些能力仍然需要保留或重新设计。

## 官方能力结论

官方实时语音识别文档把 Paraformer 归在实时 ASR 模型中，支持实时输出带标点文本、结构化时间戳、可选 VAD 和 SDK/WebSocket 接入。模型选型中，`paraformer-realtime-v2` 被推荐用于中文普通话、多语种、方言等场景。

官方 WebSocket 协议的交互模型是：

1. 客户端建立 WebSocket。
2. 发送 `run-task`。
3. 收到 `task-started` 后发送二进制音频。
4. 持续收到 `result-generated`。
5. 音频结束后发送 `finish-task`。
6. 收到 `task-finished` 后关闭连接。

这说明官方协议层返回的是任务事件和识别结果事件，不是单独的 `speech_started` / `speech_stopped` 控制事件。

对当前项目最关键的参数：

| 参数 | 作用 | 对当前链路的意义 |
| --- | --- | --- |
| `semantic_punctuation_enabled=false` | 使用 VAD 断句，关闭语义断句 | 适合低延迟交互链路 |
| `max_sentence_silence` | VAD 判断句子结束的静音阈值，默认 800ms，范围 200-6000ms | 可以作为用户结束一句话的判定来源 |
| `punctuation_prediction_enabled=true` | 自动添加标点 | 可以替代当前额外标点处理 |
| `disfluency_removal_enabled=true` | 过滤语气词 | 可用于减少“嗯、啊”等无效轮次，但要谨慎验证是否误删真实指令 |
| `inverse_text_normalization_enabled=true` | 中文数字转阿拉伯数字等 ITN | 对计时器、导航地址等任务有帮助 |
| `heartbeat=true` | 持续发送静音时保持连接不断开 | 若服务端长时间维持上行 ASR 连接，应开启并持续送静音音频 |

## 当前项目现状

当前 Vision Realtime 的 ASR 已经有 DashScope 适配器：

- 代码位置：`audio-server/realtime_agent/agent_core/providers.py`
- 类：`DashScopeAsrProviderAdapter`
- 当前参数：
  - `format="pcm"`
  - `sample_rate=16000`
  - `semantic_punctuation_enabled=False`
  - `max_sentence_silence=max_sentence_silence_ms`

但当前适配器只从 `RecognitionCallback.on_event()` 中抽取 `text` 和 `end_time`：

- `text` 非空时生成 `TranscriptEvent`
- `end_time is not None` 时认为 final

也就是说，项目当前已经部分使用了 DashScope ASR 的 VAD 断句结果，但没有暴露：

- `sentence_begin`
- `begin_time`
- `end_time`
- `sentence_end`
- `words`
- `stash`

这导致我们之前把“服务器 VAD”和“ASR final”拆成了两条复杂路径，实际上 Paraformer 可以至少承担“开始说话标记、结束一句话、ASR 文本、标点、时间戳”的大部分Vision 链路判定。

## 实验脚本

新增脚本：

```bash
tools/paraformer_realtime_probe.py
```

脚本支持两种模式：

- `sdk`：通过 `dashscope.audio.asr.Recognition` 调用，记录 SDK 回调中的原始 `RecognitionResult`。
- `websocket`：按官方 WebSocket 协议直接发送 `run-task`、音频帧和 `finish-task`，记录服务端原始 JSON 事件。
- `both`：先跑 SDK，再跑原始 WebSocket，用于对照 SDK 是否隐藏字段。

示例：

```bash
uv run python tools/paraformer_realtime_probe.py \
  --mode both \
  --input testdata/audio-sample/自我介绍一下.wav \
  --output-jsonl runs/paraformer-probe/self-intro-both-events.jsonl \
  --summary-json runs/paraformer-probe/self-intro-both-summary.json \
  --leading-silence-ms 500 \
  --trailing-silence-ms 1200 \
  --chunk-ms 100 \
  --max-sentence-silence 800
```

输出：

- JSONL：完整事件流，包含 SDK 回调和 WebSocket 原始事件。
- summary JSON：汇总服务端事件类型、是否出现显式 speech start/stop、是否出现 `sentence_begin`、首个文本时间、首个 final 时间、句子字段集合。

## 实测结果

### 音频一：`dashscope-nihao-16k.pcm`

命令：

```bash
uv run python tools/paraformer_realtime_probe.py \
  --mode both \
  --input audio-server/model-provider-tests/fixtures/provider/dashscope-nihao-16k.pcm \
  --output-jsonl runs/paraformer-probe/nihao-both-events.jsonl \
  --summary-json runs/paraformer-probe/nihao-both-summary.json \
  --leading-silence-ms 500 \
  --trailing-silence-ms 1200 \
  --chunk-ms 100 \
  --max-sentence-silence 800
```

观察：

- WebSocket 原始事件只有 `task-started`、`result-generated`、`task-finished`。
- 没有独立的 `speech_started` / `speech_stopped` 服务事件。
- 第一个 `result-generated` 中有：

```json
{
  "sentence_id": 1,
  "begin_time": 500,
  "end_time": null,
  "text": "",
  "sentence_end": false,
  "sentence_begin": true,
  "words": []
}
```

- 后续中间结果开始出现文本。
- final 结果中有：

```json
{
  "begin_time": 500,
  "end_time": 1480,
  "text": "你好。",
  "sentence_end": true
}
```

### 音频二：`自我介绍一下.wav`

命令：

```bash
uv run python tools/paraformer_realtime_probe.py \
  --mode both \
  --input testdata/audio-sample/自我介绍一下.wav \
  --output-jsonl runs/paraformer-probe/self-intro-both-events.jsonl \
  --summary-json runs/paraformer-probe/self-intro-both-summary.json \
  --leading-silence-ms 500 \
  --trailing-silence-ms 1200 \
  --chunk-ms 100 \
  --max-sentence-silence 800
```

汇总结果：

```json
{
  "service_events_seen": [
    "result-generated",
    "task-finished",
    "task-started"
  ],
  "contains_explicit_speech_start_event": false,
  "contains_explicit_speech_stop_event": false,
  "contains_sentence_begin_marker": true,
  "first_sentence_begin_ms": 1764.001,
  "first_text_event_ms": 1974.098,
  "first_final_sentence_ms": 3933.125,
  "observed_sentence_keys": [
    "begin_time",
    "channel_id",
    "end_time",
    "sentence_begin",
    "sentence_end",
    "sentence_id",
    "speaker_id",
    "stash",
    "text",
    "words"
  ]
}
```

关键原始 SDK 回调：

```json
{
  "sentence_id": 1,
  "begin_time": 900,
  "end_time": null,
  "text": "",
  "sentence_end": false,
  "sentence_begin": true,
  "words": []
}
```

final 结果：

```json
{
  "sentence_id": 1,
  "begin_time": 900,
  "end_time": 2640,
  "text": "自我介绍一下。",
  "sentence_end": true
}
```

## 对“开始说话 / 结束说话”的判断

### 用户开始说话

没有独立的 `speech_started` 服务事件。

但是实测中，Paraformer 会通过 `result-generated.payload.output.sentence.sentence_begin=true` 返回一句话开始标记，且 `text` 可以为空。SDK 的 `RecognitionCallback.on_event()` 也能拿到这个字段。

这可以映射成项目内部事件：

```text
sentence_begin=true && 当前 sentence_id 未触发过
  -> vision.vad.speech_started / input_audio_buffer.speech_started
```

限制：

- 这不是独立 VAD 回调，而是识别结果事件里的字段。
- 它到达时间晚于真实声学起点。实测 `自我介绍一下.wav` 中，音频前补 500ms 静音，模型给出的 `begin_time=900`，而 SDK 收到 `sentence_begin` 的墙钟时间约为 1764ms。
- 如果希望极低延迟打断播放，它可能比独立本地/服务端 VAD 慢一些；但它比等待第一个文字结果更早。

### 用户结束说话

可以通过 final 句子判断：

```text
sentence_end=true 或 end_time != null
  -> vision.vad.speech_stopped / input_transcript.done
```

这和官方 `max_sentence_silence` 参数一致：当语音后静音超过阈值，系统判定句子结束。

限制：

- 它判断的是“一句话结束”，不是“整个连续对话结束”。
- 长连接是否关闭仍应由 audio session 的上层策略决定，不能由 `sentence_end` 直接关闭连接。

## 引入当前项目的建议

### 建议一：先把 ASR 结果结构化，不要再只传 text/final

当前 `TranscriptEvent` 太窄，只表达：

```text
text
final
```

建议扩展为内部结构，例如：

```text
AsrRealtimeEvent
  provider
  model
  sentence_id
  text
  final
  sentence_begin
  sentence_end
  begin_time_ms
  end_time_ms
  words
  raw
```

然后由 Vision Realtime 控制器基于结构化事件转换为：

- `server.speech_started`
- `input_transcript.delta`
- `server.speech_stopped`
- `input_transcript.done`

这样模型 provider 只负责“如实上报事件”，业务链路负责解释事件。

### 建议二：Vision Realtime 默认使用 `paraformer-realtime-v2`

当前 `examples/for-blind-app/audio-server/server.yaml` 仍是：

```yaml
asr_model: "fun-asr-realtime"
```

建议切换为：

```yaml
asr_model: "paraformer-realtime-v2"
```

并配置：

```yaml
semantic_punctuation_enabled: false
max_sentence_silence_ms: 800
punctuation_prediction_enabled: true
disfluency_removal_enabled: false
inverse_text_normalization_enabled: true
heartbeat: true
```

其中 `disfluency_removal_enabled` 先不要默认开启。原因是我们当前有“嗯”“等一下”等短语作为真实打断或继续控制意图，如果直接过滤语气词，可能把有效短轮次误删。应单独用测试集验证后再开启。

### 建议三：替换现有独立 ServerVadProcessor 的主判定职责

如果采用 Paraformer 作为 Vision Realtime 的 ASR/VAD 合一 provider，则主链路应改为：

```text
上行音频 -> Paraformer Realtime Provider
  -> sentence_begin=true: server speech_started
  -> text partial: transcript delta
  -> sentence_end=true: speech_stopped + transcript done
```

独立 `ServerVadProcessor` 可以保留为可选 fallback 或本地 mock provider，不应和 Paraformer 同时驱动同一条主链路的 `speech_started` / `speech_stopped`。否则会再次出现两个来源同时取消播放、重复开关轮次、Bad file descriptor 这类竞态问题。

### 建议四：打断播放用 `sentence_begin`，不要等 transcript text

实测中 `sentence_begin=true` 早于第一个非空文本。端侧要及时暂停播放，应优先用：

```text
sentence_begin=true
```

而不是：

```text
text 非空
```

这样比 ASR 文本打断更早，也避免“用户已经开始说话但第一个字还没出来”的延迟。

### 建议五：长连接保活要显式开启 heartbeat

官方说明 `heartbeat=true` 时，持续发送静音音频可以保持连接不中断；默认不启用时，即使持续发送静音也可能 60 秒超时。

这和当前新的端侧标准模型一致：唤醒后，上行连接在连续对话期间持续发送麦克风音频。若没有用户说话，连接中会存在静音段，因此 Vision Realtime 的 Paraformer provider 应开启 heartbeat。

## 建议的实施顺序

1. 新增结构化 ASR 事件类型，先不改控制流。
2. 修改 `DashScopeAsrProviderAdapter`，保留并输出 `sentence_begin`、`begin_time`、`end_time`、`sentence_end`、`words`。
3. 在 Vision Realtime 控制器中只接受一个 speech 判定来源：
   - Paraformer provider 可用时，以 Paraformer 为准。
   - Mock provider 或非 Paraformer provider 时，才启用独立 VAD fallback。
4. 将 `server speech_started` 映射到端侧控制事件，用于暂停播放和清空播放队列。
5. 将 `sentence_end=true` 映射到用户本轮 final transcript，触发 LLM 响应。
6. 使用 `tools/paraformer_realtime_probe.py` 扩展测试集，覆盖：
   - 短口令
   - 长句
   - “嗯 / 啊 / 等一下”等打断语
   - 静音保活
   - 连续两句话

## 当前结论

`paraformer-realtime-v2` 适合引入当前 Vision Realtime 链路，并且应该作为 ASR + VAD 断句 + 标点 + 时间戳的统一 provider。

但它不是像 Omni Realtime 那样返回独立的 `input_audio_buffer.speech_started` 服务事件。它的等价信号在 `result-generated.output.sentence` 里：

- `sentence_begin=true`：可作为“用户开始说话”的 provider 信号。
- `sentence_end=true` 或 `end_time != null`：可作为“用户一句话结束”的 provider 信号。

因此，项目内部需要做一层事件归一化，把 Paraformer 的句子字段转换成统一的 server-side speech events，再下发给端侧。

## 参考资料

- 阿里云百炼：Paraformer 实时语音识别 WebSocket API  
  https://help.aliyun.com/zh/model-studio/websocket-for-paraformer-real-time-service
- 阿里云百炼：实时语音识别 Fun-ASR/Paraformer  
  https://help.aliyun.com/zh/model-studio/real-time-speech-recognition
