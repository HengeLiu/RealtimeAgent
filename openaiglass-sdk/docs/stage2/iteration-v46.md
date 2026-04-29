# SDK v46 迭代记录

## 背景

TTS 首包诊断显示，首次 CosyVoice WebSocket 建连和首次 `streaming_call(...)` 会带来约数百毫秒耗时。此前 SDK 在首个模型 token 到达后才创建 TTS 会话，导致这段耗时叠加在首听延迟上。

## 变更

1. `VoiceRuntime` 在调用 `AgentFacade.handle_turn(...)` 前创建最终回复的 `ReplySynthesisContext` 和流式 TTS session。
2. 新增 `TTS 预热已启动` 日志，标记 TTS WebSocket 预热开始，并携带本轮回复 `stream_id`。
3. 首个模型文本增量到达时，直接复用已预热 TTS session 推送文本。
4. 如果预热 session 因模型首 token 或工具链路耗时过长而失效，首次推送失败时会记录 `TTS 预热会话推送失败，重建后重试`，然后重建 session 并重试当前文本。
5. 如果 Agent 没有返回流式文本增量，SDK 会把最终回复文本推入已经预热的 TTS session，不再重新走一条未预热 TTS 路径。

## 预期效果

大模型首 token 等待期间可以并行完成 CosyVoice WebSocket 建连，降低首 token 到首段 TTS 音频之间的可见延迟。实际收益取决于模型首 token 耗时和百炼 TTS 服务端是否会等待足够文本后才返回首段音频。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
3. 使用本地假 `VoiceModelClient` / `AgentFacade` 执行 `_run_model_pipeline(...)`，确认 TTS session 在 Agent `handle_turn(...)` 前创建，首个文本 delta 复用同一 session。
