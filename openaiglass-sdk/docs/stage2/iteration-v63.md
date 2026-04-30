# iteration-v63：Omni 流式上行失败时停止重连放大

对应对外 SDK 版本：`sdk-v63`。

## 背景

真机联调中出现以下链路：

1. Omni Realtime 预连接 WebSocket 已经关闭。
2. 服务端仍在用户说话过程中继续追加音频，日志出现 `Connection is already closed`。
3. `segment.finished` 后，因为 semantic VAD 没有自动提交，服务端立刻改用 `segment_turn` 重新连接兜底。
4. 百炼返回 `Too many requests. Your requests are being throttled due to system capacity limits`。

这说明当前问题已经不是单纯的端侧回声误触发，而是上游 Realtime 服务连接关闭后，本地兜底策略又额外创建新连接，放大了容量限制下的请求压力。

## 本轮改动

1. `OmniRealtimeStreamingSession.append_audio()` 记录流式上行失败状态和异常原因。
2. semantic VAD 收尾时，如果本轮已经发生过流式上行失败，服务端不再切换到 `segment_turn` 重连兜底。
3. 新增日志 `Omni semantic_vad 流式上行失败，跳过 segment_turn 重连兜底`，用于区分：
   - `semantic_vad` 正常未自动提交，可以兜底；
   - WebSocket 已关闭或上行失败，不应立即重连兜底。
4. 返回结构化错误 `omni_realtime_audio_push_failed`，让调用方知道这是上游连接问题，不是用户语音内容问题。

## 验证

1. 新增单元测试模拟第一次音频上行成功、第二次音频上行失败。
2. 测试确认 `finish()` 抛出 `omni_realtime_audio_push_failed`，而不是 `semantic_vad_no_auto_response`。
3. 因为错误原因不同，`_run_omni_realtime_reply_pipeline()` 不会进入 `segment_turn` 重连兜底分支。

## 真机观察点

如果再次出现上游 WebSocket 关闭：

1. 服务端仍可能打印 `Omni Realtime 上行音频推送失败`。
2. 随后应打印 `Omni semantic_vad 流式上行失败，跳过 segment_turn 重连兜底`。
3. 不应再紧跟着出现 `Omni semantic_vad 未生成自动响应，改用 segment_turn 重连兜底`。
4. 不应因为同一段音频继续创建新 Omni WebSocket 并触发 `Too many requests`。

## 风险和后续

1. 这轮修复会在上游连接已坏时放弃本轮回复，换取不继续放大限流。
2. 如果百炼 Realtime 服务持续返回容量限制，需要增加全局冷却窗口，短时间内暂停新 Omni 连接，避免多轮语音连续失败。
3. 如果真实插话场景对可靠性要求更高，可考虑在冷却窗口内临时降级到 `agent_tts + asr_text`，但这会重新引入独立 ASR 和 TTS 延迟。
