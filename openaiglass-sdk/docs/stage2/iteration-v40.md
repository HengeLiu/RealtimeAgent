# iteration-v40：SDK v41 实时 ASR 分段延迟诊断与 VAD 阈值

## 本轮目标

继续排查 `fun-asr-realtime` 首个 ASR 结果约 1 秒的问题，把实时 ASR 链路拆成更细的可观测阶段，并降低官方 Recognition 的句尾静音阈值，减少用户说完后的收尾等待。

本轮对应对外 SDK 版本：`sdk-v41`。

## 主要改动

1. `DashscopeRealtimeSpeechRecognitionSession` 记录实时 ASR 会话创建、连接打开、首个音频 chunk、首个 `send_audio_frame(...)` 返回、首个文本回调、stop 请求和 complete 回调的时间。
2. `实时 ASR 返回首个文本` 日志新增累计音频时长、已发帧数、已发字节数和 DashScope SDK 的 `get_first_package_delay()`。
3. `实时 ASR 完成` 日志新增 `recognition_open_latency_ms`、`session_start_to_first_audio_ms`、`first_audio_send_cost_ms`、`audio_ms_before_first_partial`、`dashscope_first_package_delay_ms`、`dashscope_last_package_delay_ms`、`stop_to_complete_ms`、`audio_frame_count` 和 `audio_bytes_sent`。
4. 新增 `VOICE_ASR_REALTIME_MAX_SENTENCE_SILENCE_MS` 配置，默认 `300`，传给官方 Recognition 的 `max_sentence_silence`。

## 排查方法

1. 如果 `recognition_open_latency_ms` 很高，瓶颈在实时 ASR WebSocket 建连或 SDK 启动。
2. 如果 `first_audio_send_cost_ms` 很高，瓶颈在本地 SDK 发帧或线程阻塞。
3. 如果 `audio_ms_before_first_partial` 接近 1000ms，说明 ASR 服务本身需要约 1 秒语音才返回首个文本。
4. 如果 `stop_to_complete_ms` 接近原来的 1300ms，说明句尾 VAD 静音阈值是主要问题，可继续调小 `VOICE_ASR_REALTIME_MAX_SENTENCE_SILENCE_MS`。

## 验证

```bash
PYTHONPATH=openaiglass-sdk/server-python \
uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v

PYTHONPATH=openaiglass-sdk/server-python \
uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_server_cli.py -q
```
