# SDK 迭代记录：Omni semantic_vad 默认连续对话

对应对外 SDK 版本：`sdk-v55`。

## 背景

`sdk-v54` 已完成 Omni `semantic_vad` 的配置、协议和会话参数接线，但默认仍是 `segment_turn`。本轮继续把方案二推进到默认链路：用户一次 WakeNet 唤醒后，真实 `glass-esp32` 打开短时间连续对话窗口，服务端把上行音频和本轮自动照片交给 Omni Realtime，由 Omni 自动判断 turn 并直接返回语音。

官方依据见 Qwen-Omni-Realtime 文档：该模型支持 WebSocket 实时会话、流式音频与图片输入、`semantic_vad` turn detection，以及 `response.audio.delta` 音频增量输出。

## 本轮变更

1. 默认 `VOICE_CONVERSATION_MODE` 改为 `realtime_semantic_vad`，保留 `segment_turn` 作为回退模式。
2. 服务端在 `sensor.audio.segment.started` 时提前启动本轮自动抓拍，照片就绪后等待至少一段音频已追加到 Omni，再异步追加图片，避免官方接口报错“append image before append audio”。
3. `OmniRealtimeStreamingSession.finish(...)` 在 semantic_vad 模式下不再调用 `commit()` 和 `create_response(...)`，只等待 Omni 自动提交和自动响应。
4. Omni 事件回调补充 `speech_started`、`speech_stopped`、`input_audio_buffer.committed`、`response.created` 等观测点，便于区分模型等待、VAD 提交和首段音频延迟。
5. `glass-esp32` 在收到服务端声明的 `realtime_semantic_vad` 后，向服务端上报连续对话能力；一次 WakeNet 命中后打开 30 秒连续对话窗口，后续可由本地 VAD 直接触发下一段语音。
6. 更新 SDK 开发指南、设计文档、`local_server.env.example` 和 `sdk-version`。

## 当前边界

1. ESP32 固件当前仍上报 `aec=false`、`barge_in=false`，因此播放期间保持半双工，避免助手声音被麦克风回灌给模型。
2. 本轮支持“助手播完后的自然追问”，尚不支持播放中自然插话。播放中插话需要端侧 AEC 或可靠回声抑制后再开启。
3. Omni Realtime 直出分支仍不执行 SDK Tool、Task、Skill、MCP；需要业务编排时应使用 `VOICE_REPLY_MODE=agent_tts` 或后续接入 Omni Function Calling。
4. `glass-playback` 仍只用于协议回放和验收，不代表真实麦克风、AEC、旁人说话过滤或室外噪声效果。

## 验证

1. 单元测试覆盖 semantic_vad 模式下不手动 commit/create response，确保等待 Omni 自动响应。
2. 单元测试覆盖默认配置、实时语音协议 payload 和旧 `segment_turn` 分支的回归。
3. ESP32 固件本轮做源码级实现，实际 AEC、播放中插话和嘈杂环境效果需要真机联调继续验证。

## 后续计划

1. 评估并接入 ESP32 端 AEC 或同等回声抑制能力。
2. 在具备 AEC 后实现播放中插话：端侧触发 interrupt，服务端 cancel Omni response 并中断下行播放。
3. 增加多 turn `glass-playback` 时间线回放，用于协议和回归验收。
4. 为室外嘈杂环境增加 VAD profile 和误触发保护策略。
