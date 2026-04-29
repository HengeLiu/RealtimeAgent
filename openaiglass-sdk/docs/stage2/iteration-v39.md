# iteration-v39：SDK v40 实时 ASR 切换官方 Recognition 接口

## 本轮目标

把实时 ASR 从原来的 Qwen Omni Realtime 转写路径切换到阿里云百炼官方实时语音识别接口，确保 `fun-asr-realtime` 按文档要求工作。

本轮对应对外 SDK 版本：`sdk-v40`。

## 问题判断

上一版虽然在服务端收到首个音频 chunk 时开始打点，但实时 ASR 实现仍使用 `dashscope.audio.qwen_omni.OmniRealtimeConversation`，并在音频结束后 `commit()`。这不是 `fun-asr-realtime` 官方文档中的 `Recognition.start()`、`send_audio_frame(...)`、`RecognitionCallback.on_event(...)` 链路，因此首段文本和总耗时容易表现为完全一致。

## 主要改动

1. `DashscopeRealtimeSpeechRecognitionSession` 改为使用 `dashscope.audio.asr.Recognition`。
2. 会话启动时调用 `Recognition.start()`。
3. 每个眼镜上行 PCM chunk 到达后立即调用 `send_audio_frame(...)`，不再先进入 SDK 自己的 ASR 发送队列。
4. 语音结束时调用 `Recognition.stop()`，等待 `on_complete()`。
5. `on_event(...)` 读取 `RecognitionResult.get_sentence()`，用 `end_time` 是否存在判断最终句子。
6. 默认 `VOICE_ASR_REALTIME_MODEL_NAME` 改为 `fun-asr-realtime`。

## 指标口径

| 字段 | 起点 | 终点 |
| --- | --- | --- |
| `first_asr_partial_latency_ms` | 服务端收到眼镜第一个音频 chunk，并调用 `send_audio_frame(...)` | `RecognitionCallback.on_event(...)` 收到第一段非空文本 |
| `asr_total_latency_ms` | 服务端收到眼镜第一个音频 chunk，并调用 `send_audio_frame(...)` | `RecognitionCallback.on_complete(...)` 收到完成事件 |

如果两者仍然完全一致，优先判断 ASR 服务是否只在句尾返回文本，而不是 SDK 仍走非实时链路。

## 验证

```bash
PYTHONPATH=openaiglass-sdk/server-python \
uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v
```
