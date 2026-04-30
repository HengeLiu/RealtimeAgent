# iteration-v61：Omni 语义确认播放中插话

对应对外 SDK 版本：`sdk-v61`。

## 背景

`sdk-v60` 在 ESP32-S3 端加入播放起始保护窗后，真机仍会在播放首段 PCM 写入扬声器约 1.1 秒后误触发 `user.voice.interrupt`。日志表现为每次只播放一点声音就被端侧本地 VAD 打断，然后启动下一段候选语音并循环。

对照 Qwen Omni Realtime 文档后，本轮调整策略：本地 VAD 不再直接代表真实插话，只作为候选段启动信号。真正的播放打断交给 Omni `semantic_vad` 确认。

## 本轮改动

1. `glass-esp32` 播放中 VAD 命中后，只打印“候选语音段”并继续上传音频，不再立即发送 `user.voice.interrupt`，也不再本地停止播放。
2. 服务端为 Omni Realtime 会话新增 `input_audio_buffer.speech_started` 和 `input_audio_buffer.committed` 回调。
3. 播放中启动的候选段只有在收到 Omni `input_audio_buffer.speech_started` 后，才调用统一播放打断逻辑中断旧播放。
4. 播放中候选段如果到 `segment.finished` 时仍没有触发 Omni 自动提交，服务端按播放回声候选丢弃，不再走 `segment_turn` 重连兜底，避免误触发循环生成新回复。
5. 非播放中的普通语音段仍保留 `segment_turn` 兜底，防止 Omni `semantic_vad` 偶发不自动提交时整轮失败。

## 验证

1. 单元测试覆盖播放中候选段不会在 `segment.started` 时立即打断。
2. 单元测试覆盖播放中候选段未被 Omni 确认为语音时会被丢弃，不会发送新回复。
3. 真机需要继续观察：播放开始后如果只是扬声器回声，不应再出现 `user.voice.interrupt`；真实用户插话时应先看到服务端日志 `Omni semantic_vad 确认播放中用户插话`，随后才出现播放中断。

## 风险和后续

1. 真正插话的响应速度会取决于 Omni `input_audio_buffer.speech_started` 返回速度，不再完全由本地 VAD 决定。
2. 如果室外噪声被 Omni 误判为用户语音，需要继续调 `VOICE_REALTIME_SEMANTIC_VAD_THRESHOLD`、端侧候选 VAD 阈值和麦克风/扬声器结构。
3. 如果 Omni 长时间不返回 `speech_started`，可以考虑增加一个更高置信度的端侧兜底，但默认路径应优先相信模型侧语义 VAD。
