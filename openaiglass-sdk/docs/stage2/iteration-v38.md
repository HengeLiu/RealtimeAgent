# iteration-v38：SDK v39 实时 ASR 延迟指标口径修正

## 本轮目标

修正实时 ASR 首文本耗时日志的起点，避免把实时 ASR 会话创建、控制消息时序或首个音频 chunk 之前的等待时间算入 `first_asr_partial_latency_ms`。

本轮对应对外 SDK 版本：`sdk-v39`。

## 主要改动

1. `DashscopeRealtimeSpeechRecognitionSession` 在收到第一段非空音频 chunk 时记录 `first_audio_chunk_at_ms`。
2. `first_asr_partial_latency_ms` 改为从 `first_audio_chunk_at_ms` 到 ASR 服务返回第一段文本的耗时。
3. `实时 ASR 完成` 日志新增 `asr_total_latency_ms`，从首个音频 chunk 到 ASR 最终文本完成。
4. `StreamingSpeechRecognitionSession` 增加 `metrics()` 扩展面，方便 mock 或其他 ASR 实现输出一致的延迟指标。

## 指标口径

| 字段 | 起点 | 终点 |
| --- | --- | --- |
| `first_asr_partial_latency_ms` | 服务端收到眼镜第一个音频 chunk，并送入实时 ASR session | ASR 服务返回第一段文本 |
| `asr_total_latency_ms` | 服务端收到眼镜第一个音频 chunk，并送入实时 ASR session | ASR 服务返回最终完整文本 |

这两个指标不包含设备注册、语音会话打开、绑定等待和 `sensor.audio.segment.started` 到首个 `/ws_audio` chunk 之间的空档。

## 验证

```bash
PYTHONPATH=openaiglass-sdk/server-python \
uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v
```
