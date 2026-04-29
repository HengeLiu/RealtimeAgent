# SDK v45 迭代记录

## 背景

TTS 首段音频日志只显示了“首个模型文本增量到 SDK 播放队列首段音频”的总耗时，无法区分耗时发生在 CosyVoice WebSocket 建连、首次文本推送、百炼 TTS 服务首包回调，还是 SDK 回调后的重采样和入队。

## 变更

1. `DashscopeCosyVoiceTtsSession` 增加 TTS WebSocket 打开时间日志：`TTS WebSocket 已打开`。
2. 首次调用 `streaming_call(text_delta)` 后打印 `TTS 首次文本已推送`，包含本地调用耗时、session 创建到首次推送耗时、WebSocket 打开到首次推送耗时。
3. 首个 `on_data(...)` 音频回调到达时打印 `TTS 服务返回首段音频`，包含：
   - `tts_first_audio_latency_ms`：首次文本推送开始到 TTS 首段音频回调。
   - `tts_first_audio_after_call_return_ms`：首次 `streaming_call(...)` 返回后到 TTS 首段音频回调。
   - `session_create_to_first_audio_ms`：TTS session 创建到首段音频回调。
   - `websocket_open_to_first_audio_ms`：WebSocket 打开到首段音频回调。
   - `text_chars_before_first_audio` / `text_push_count_before_first_audio`：首段音频前已经推给 TTS 的文本量。
4. 原有 `TTS 返回首段音频` 保留，继续表示首段音频进入 SDK 后完成重采样并放入播放队列的时间。

## 结论

当前 SDK 本地 TTS 热路径没有图片、上下文或模型消息级重处理。首包耗时需要通过 `TTS 服务返回首段音频` 与 `TTS 返回首段音频` 对比判断：如果前者已经很大，耗时主要在 TTS 服务首包；如果两者差距大，才说明 SDK 回调后处理或重采样入队存在问题。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
2. `git diff --check -- openaiglass-sdk/server-python/runtime/voice_runtime.py`
