# sdk-v96 Omni Realtime 事件日志收敛

更新时间：2026-05-04

## 背景

真机连续对话日志中，Omni Realtime 会把每个 `response.audio.delta` 音频分片都打印为 DEBUG 日志。每个分片只包含音频 base64 长度摘要，但频率很高，会淹没 `response.audio.done`、`response.done`、工具调用和播放状态等关键事件。

同一批日志里还出现了：

```text
ERROR-dashscope Request failed ... request timeout after 23 seconds.
```

从事件顺序看，主链路已经收到 `response.audio_transcript.done`、`response.audio.done` 和 `response.done`，并且后续仍能进入下一轮对话。因此这不是 Omni 回复缺少结束事件，更像是 DashScope SDK 内部某条后台请求或旁路实时 ASR 收尾超时打印出的供应商日志。该异常不应被误判为当前回复没有完成。

## 变更

1. 不再逐帧打印 `response.audio.delta` server event。
2. 保留 `response.audio.done`、`response.done`、工具调用、输入语音事件和错误事件日志。
3. `session.created` / `session.updated` 的日志 payload 改为摘要，只记录模型、音色、turn detection、工具数量和 instructions 长度，不再把完整系统提示词和工具 schema 打进日志。
4. 更新单测，验证 `response.audio.done` 仍可收口播放流，同时确认音频 delta 摘要不再出现在日志里。

## 对业务开发者的影响

业务能力代码不需要修改。

真机排障时，如果看到模型回复后仍有 DashScope timeout，但同一轮已经出现 `response.audio.done` / `response.done`，应优先判断为后台旁路链路或供应商 SDK 收尾日志，不要直接认为 Omni 主回复没有结束。

## 验证

```bash
uv run python -m unittest openaiglass-sdk.tests.unit.test_voice_runtime -v
```

结果：53 个语音运行时单测全部通过。
