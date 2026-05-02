# SDK 迭代记录：Omni 音频直出旁路 ASR 转写

对应对外 SDK 版本：`sdk-v77`。

## 背景

`sdk-v76` 已把眼镜 PCM 上行改成真正的字节流：端侧开始发送音频后，服务端立即把分片推给 Omni Realtime。但用户文本仍主要来自 Omni Realtime 自己返回的 transcript 事件，这会把“回答模型”和“转写来源”绑定在一起。

本轮把转写拆成旁路能力：Omni 继续直接消费音频字节流并返回音频回复；ASR 作为 sidecar 节点并行接收同一份 PCM，用于日志、会话上下文和后续记忆输入。

## 本轮变更

1. `SegmentBuffer` 新增旁路 ASR 状态：
   - `sidecar_asr_session`
   - `sidecar_transcript_done`
   - `sidecar_transcript_text`
   - `sidecar_transcript_source`
   - `sidecar_transcript_error`
   - `sidecar_asr_metrics`
2. `VOICE_REPLY_MODE=omni_realtime` 且输入是原始音频时，`VoiceRuntime` 会在启动 Omni 输入流后启动旁路 ASR。
3. `/ws_audio` 每个 PCM 分片会 fan-out 到三个位置：
   - 本地 `SegmentBuffer`，用于落盘和兜底。
   - Omni Realtime 会话，作为主回答链路。
   - 旁路 ASR 会话，作为异步转写链路。
4. `sensor.audio.segment.finished` 后：
   - Omni 主链路不等待 ASR，继续 commit 并请求音频回复。
   - 旁路 ASR 在后台 finish；如果结果已就绪，则优先写入 Agent-Core。
   - 如果旁路 ASR 晚于 Omni 回复完成，会异步回填 Agent 会话中的用户消息和 transcript artifact。
5. Transcript artifact 和文字交互日志新增来源字段：
   - `transcript_source=sidecar_realtime_asr`
   - `transcript_source=sidecar_batch_asr`
   - `transcript_source=omni_fallback`
   - `transcript_source=unavailable`
6. 保留降级逻辑：实时旁路 ASR 未启用或启动失败时，段结束后尝试批量 ASR 旁路转写；旁路失败不影响 Omni 主回答链路。

## 验证

1. 单元测试：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q
```

结果：`71 passed`。

2. 设备级回放：

```bash
LOG_LEVEL=DEBUG uv run openaiglass.server.start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env \
  --log-file openaiglass-for-blind/logs/server-sidecar-asr-check.log

uv run openaiglass.phone.mock \
  --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json

uv run openaiglass.glass.start \
  --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/look_look.json
```

结果：`glass-playback` 返回 `assertions_ok=true`，`actuator_count=1`。

关键日志：

```text
2026-05-01T13:51:00.919352+00:00 glass-playback 开始发送触发音频 chunks=119
2026-05-01T13:51:01.217123+00:00 server.voice Omni Realtime 首段上行音频已推送 bytes=8960 frame_count=1
2026-05-01T13:51:01.217181+00:00 server.voice Omni Realtime 端到端输入流已启动 buffered_audio_bytes=8960 tool_count=6
2026-05-01T13:51:01.219425+00:00 server.voice 旁路 ASR 实时输入流已启动 buffered_audio_bytes=8960
2026-05-01T13:51:06.176986+00:00 glass-playback 触发音频发送完成 bytes=151552
2026-05-01T13:51:06.187663+00:00 server.voice Omni Realtime 请求已提交 audio_bytes=151552 audio_frame_count=113 image_count=1
2026-05-01T13:51:06.230186+00:00 server.voice 旁路 ASR 转写完成 source=sidecar_realtime_asr text='我叫文刀。文字的文，刀锋的刀。'
2026-05-01T13:51:06.831067+00:00 server.voice Omni Realtime 返回首段音频 bytes=15360
2026-05-01T13:51:07.106568+00:00 server.voice Omni Realtime 文字交互 user='我叫文刀。文字的文，刀锋的刀。' transcript_source=sidecar_realtime_asr
```

这组日志确认：回答主链路仍走 Omni 输入字节流，转写文本来自旁路 ASR，不再依赖 Omni transcript。

验证结束后已停止本地 server 和 phone mock。
