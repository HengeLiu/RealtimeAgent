# SDK 迭代记录：Omni 默认链路与说话期间预推音频

对应对外 SDK 版本：`sdk-v52`。

## 背景

`sdk-v51` 已经明确 `VOICE_INPUT_MODE`，但 Omni Realtime 分支仍在 `sensor.audio.segment.finished` 之后才建连、追加整段音频、追加图片并提交请求。真实日志显示用户说完后到首段模型音频之间仍有明显串行耗时，其中一部分来自建连和整段音频提交。

## 本轮变更

1. 默认语音回复模式从 `agent_tts` 切换为 `omni_realtime`：
   - 默认 `VOICE_INPUT_MODE=auto`，因此实际输入模式为 `raw_audio`。
   - 需要 Tool、Task、Skill、MCP 或长期记忆编排时，开发者应显式配置 `VOICE_REPLY_MODE=agent_tts`。
2. `VoiceRuntime` 在 `sensor.audio.segment.started` 时预启动 Omni Realtime：
   - 预先建立 WebSocket。
   - 预先创建下行播放上下文。
   - 建连失败时只记录 DEBUG，结束阶段回退到普通提交路径。
3. `/ws_audio` 每个音频 chunk 到达时，除写入本地 `SegmentBuffer` 外，也同步追加到 Omni Realtime 会话。
4. `sensor.audio.segment.finished` 后只执行：
   - 等待自动照片。
   - 追加图片。
   - `commit()` 和 `create_response()`。
   - 等待 `response.audio.delta` 并流式下发播放。
5. 新增日志：
   - `Omni Realtime 预连接已建立`
   - `Omni Realtime 首段上行音频已推送`
   - `Omni Realtime 请求已提交`

## 延迟预期

这轮优化主要去掉用户说完后的建连和整段音频一次性追加开销。剩余首听延迟主要来自：

1. `VOICE_OMNI_PHOTO_WAIT_MS` 等待自动照片的时间。
2. `commit/create_response` 后 Omni 服务首段音频返回时间。
3. HTTP 播放流首包和端侧播放器写入时间。

## 边界

当前仍按半双工语音段边界提交响应：服务端不会在用户尚未说完时请求模型开始回答。真正全双工响应、用户插话打断和服务端 VAD 模式仍需要端侧 AEC/VAD 能力继续配合。
