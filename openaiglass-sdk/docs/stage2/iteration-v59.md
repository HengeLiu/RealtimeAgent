# iteration-v59：SDK v59 插话后 Omni 回复收敛

对应对外 SDK 版本：`sdk-v59`。

## 背景

`sdk-v58` 后真实 ESP32-S3 已经可以在播放中触发插话：端侧会上报 `user.voice.interrupt`，本地播放流会断开，服务端也会进入播放仲裁器。但真机日志继续暴露出两个服务端问题：

1. 被用户插话打断的旧 Omni 响应仍可能在后台完成，并继续发送最终 `assistant.reply`、写入对话上下文。
2. 新一轮语音如果没有触发 Omni `semantic_vad` 自动提交，服务端在同一个 semantic VAD WebSocket 上手动 `commit/create_response` 可能触发上游 1011 或 `Internal service error: null`。

## 本轮改动

1. 服务端在 Omni 回复完成后会检查对应播放流是否已被用户插话打断；如果已打断，只保留必要的播放流收尾，不再发送迟到的 `assistant.reply`，也不把旧回复写入 `message_context`。
2. `OmniRealtimeStreamingSession.finish(...)` 在 `realtime_semantic_vad` 模式下不再对没有自动提交的会话手动 `commit/create_response`，而是抛出可识别的兜底信号。
3. `VoiceRuntime` 捕获该兜底信号后，会关闭当前 semantic VAD 会话，并用同一段完整 PCM 和本轮图片重连一次 `segment_turn` 模式，避免在不稳定的上游状态里继续提交。
4. 如果播放流已经被插话打断，服务端不会再为旧段执行 `segment_turn` 兜底，避免旧段和新段抢占播放或上下文。

## 验证

1. 单元测试覆盖 Omni `semantic_vad` 无自动响应时不再手动提交，并返回 `segment_turn_reconnect` 兜底信号。
2. 单元测试覆盖现有 Omni 音频增量、semantic VAD 参数、播放仲裁和下行首包观测路径。
3. 仍需要真机复测：播放中插话后，旧 `reply_*` 应只出现播放中断日志，不应再出现旧回复的 `assistant.reply`；新一轮如果没有 `input_audio_buffer.speech_stopped`，应出现 `改用 segment_turn 重连兜底`，并继续得到响应。

## 风险和后续

1. `segment_turn` 重连兜底会增加一次 WebSocket 建连开销，通常约 200ms，但比 45 秒超时或上游 1011 更可控。
2. 如果 semantic VAD 经常不自动提交，仍应继续调 `VOICE_REALTIME_SEMANTIC_VAD_THRESHOLD`、静音时长、端侧 VAD 和 AEC 参考同步。
3. 后续可以继续接入 Omni response 主动取消接口；当前版本先从播放流和上下文层面丢弃被插话打断的旧回复。
