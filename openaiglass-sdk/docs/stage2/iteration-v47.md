# SDK v47 迭代记录

## 背景

`sdk-v46` 已经把最终回复的 TTS session 创建提前到 Agent 请求之前，但真实联调日志显示 DashScope `SpeechSynthesizer` 仍在首次 `streaming_call(...)` 时才打开 WebSocket 并启动流式任务。因此 `TTS WebSocket 已打开` 仍出现在大模型首 token 之后，首次文本提交还会承担建连和 run-task 握手耗时。

## 变更

1. `DashscopeCosyVoiceTtsSession` 创建后会启动后台预热线程。
2. 预热线程提前启动 CosyVoice 流式任务，让 WebSocket 建连和 run-task 握手与 Agent 首 token 等待并行发生。
3. 首个文本增量到达时，SDK 会优先复用已经预启动的流式任务，只提交文本。
4. 新增 `TTS 预热流已启动` 日志，携带 `prewarm_stream_cost_ms`、`session_create_to_prewarm_stream_ms` 和 `session_create_to_open_ms`。
5. 如果预热失败，SDK 退化为首次文本触发建连；如果首次推送失败，上层仍会记录 `TTS 预热会话推送失败，重建后重试` 并重建 TTS session。

## 预期效果

正常情况下，服务端日志中 `TTS WebSocket 已打开` 和 `TTS 预热流已启动` 应出现在 `大模型返回首个 token` 之前。首个文本到来后的 `first_streaming_call_cost_ms` 应明显下降，剩余 `tts_first_audio_after_call_return_ms` 主要反映百炼 TTS 服务在收到文本后返回首段音频的耗时。

## 风险和边界

本轮为了压低首包延迟，使用了 DashScope Python SDK 的内部流启动能力。SDK 已保留失败退化和重建重试，但如果后续 DashScope SDK 改动内部方法名，预热会退化为首次文本触发，不会阻断语音主链路。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
3. 使用真实服务端观察 TTS 日志顺序：`TTS 预热已启动`、`TTS WebSocket 已打开`、`TTS 预热流已启动` 应在首个模型文本增量之前出现。
