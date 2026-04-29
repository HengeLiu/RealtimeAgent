# iteration-v58：SDK v58 连续对话与插话链路修复

对应对外 SDK 版本：`sdk-v58`。

## 背景

真实 ESP32-S3 眼镜在 `sdk-v57` 可以完成首轮 Omni 连续对话，但真机日志暴露出三个问题：

1. 服务端过早收到 `voice.realtime.session.opened`，此时端侧 AFE/AEC 还未初始化完成，导致服务端把会话降级为 `half_duplex`。
2. 首轮服务端已经开始回复后，端侧本地语音段仍持续采集到最大 8 秒，播放声会让本地 VAD 长时间保持非静音，播放中插话没有机会启动新段。
3. 第二轮语音里 Omni `semantic_vad` 可能没有自动提交，服务端只等待自动响应会拖到 45 秒超时。

## 本轮改动

1. `glass-esp32` 在 AFE 初始化完成后，如果当前是实时语义连续对话会话，会补发一次 `voice.realtime.session.opened`，用最新 `accepted_mode` 和 `capabilities.aec` 覆盖早期半双工能力。
2. `glass-esp32` 在收到服务端下行播放时，会强制结束当前仍在采集的本地语音段，使用 `finish_reason=server_response_started`，避免旧段占住 VAD 状态机。
3. `glass-esp32` 每个本地语音段都会生成新的 `stream_id`，避免连续对话多轮复用同一个上行流编号。
4. 服务端允许 `omni_realtime + realtime_semantic_vad` 模式下在播放期间收到新的 `sensor.audio.segment.started`，并通过统一播放仲裁器打断当前播放。
5. 服务端 `OmniRealtimeStreamingSession.finish(...)` 在 `semantic_vad` 没有自动提交时，会在 `segment.finished` 后手动 `commit()` 并 `create_response(...)`，作为连续对话兜底路径。

## 验证

1. 单元测试覆盖 ESP32 固件静态边界：实时能力补发、服务端回复开始后提前关闭本地段、每段刷新 `stream_id`。
2. 单元测试覆盖 Omni `semantic_vad` 无自动响应时的手动提交兜底。
3. 仍需要真机复测：首次 WakeNet 后服务端应更新到 `accepted_mode=full_duplex_realtime`，播放中说话应触发 `user.voice.interrupt` 或服务端段开始兜底打断，第二轮不应再等待 45 秒超时。

## 风险和后续

1. AEC 声学效果仍取决于硬件布局、扬声器音量和播放参考同步；本轮只修正协议状态和本地段生命周期。
2. 如果 Omni `semantic_vad` 经常无法自动提交，需要继续调 `VOICE_REALTIME_SEMANTIC_VAD_THRESHOLD`、静音时长和端侧 VAD 参数。
