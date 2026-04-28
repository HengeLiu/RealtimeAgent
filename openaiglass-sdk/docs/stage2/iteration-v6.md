# SDK v7 迭代记录

本文记录 SDK 团队在 `sdk-v6` 之后，按欠缺能力优先级推进的第二轮能力补全。业务侧版本记录更新为 `sdk-v7`。

## 1. 输入反馈

本轮处理“普通文本流式和 TTS 首包延迟”。此前代码虽然调用了 `Runner.run_streamed(...)`，但普通文本回复的 `response.output_text.delta` 没有继续透传给 `reply_text_delta_callback`。因此普通问答仍容易等完整回复出来后才进入 TTS。

本轮只处理普通文本 delta 到 TTS 调度层的透传和首包观测，不处理：

1. 全双工实时语音。
2. 用户语音打断。
3. 通知抢播和播放仲裁。
4. TTS 服务商底层 WebSocket 性能优化。

## 2. 本轮 SDK 改动

### 2.1 普通文本 delta 透传

`OpenAIAgentLoopRunner._run_streamed_turn(...)` 现在会处理 Agents SDK 的 `raw_response_event`：

```text
event.type = raw_response_event
event.data.type = response.output_text.delta
event.data.delta = 文本增量
```

提取到的普通文本增量会：

1. 追加到当前轮 `reply_text_parts`。
2. 立即调用 `reply_text_delta_callback(text_delta)`。
3. 最终优先由流式增量拼接出 `AgentTurnResult.reply_text`。

这样普通问答和图片解读主链路都能走同一个上层流式 TTS 回调。

### 2.2 首包延迟观测

`PlaybackStreamContext` 新增：

1. `first_text_delta_at_ms`
2. `first_audio_chunk_at_ms`
3. `first_play_request_at_ms`

`VoiceRuntime.build_runtime_snapshot()` 新增：

1. `reply_first_text_delta_at_ms`
2. `reply_first_audio_chunk_at_ms`
3. `reply_first_play_request_at_ms`
4. `reply_text_to_first_audio_ms`
5. `reply_audio_to_play_request_ms`

这些字段用于判断普通回复是否真的进入流式 TTS，以及首音频延迟发生在 Agent 文本、TTS 合成还是播放请求阶段。

## 3. 本轮不进入 SDK 的内容

1. 如果当前环境回退到 `BufferedStreamingTtsSession`，TTS 仍会在 `finish()` 后生成音频；此时快照中的 `reply_text_to_first_audio_ms` 会暴露延迟。
2. 本轮没有修改眼镜端播放协议。
3. 本轮没有修改通知和打断策略，播放期间是否允许用户语音打断仍由后续专项处理。

## 4. 文档同步

已同步更新：

1. `openaiglass-sdk/docs/structure-design/普通文本流式与TTS首包延迟优化设计.md`
2. `openaiglass-for-blind/SDK安装与能力开发指南.md`
3. `openaiglass-for-blind/sdk-version`

## 5. 验证范围

新增测试覆盖：

1. 普通 `raw_response_event` 文本增量会进入 `reply_text_delta_callback`。
2. 最终回复文本优先使用流式文本增量拼接结果。
3. `VoiceRuntime` 运行态快照会记录首文本、首音频、首播放请求和延迟字段。

验证命令：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_agent_core.py openaiglass-sdk/tests/unit/test_voice_runtime.py -q
python -m compileall -q openaiglass-sdk/server-python/agent_core/runtime openaiglass-sdk/server-python/runtime
```
