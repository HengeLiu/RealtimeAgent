# SDK 迭代记录：Omni 语义实时连续对话接线

对应对外 SDK 版本：`sdk-v54`。

## 背景

真实语音对话如果每轮都要求唤醒词，室外和连续追问场景体验很差。当前 `sdk-v52` 的 Omni Realtime 已经把建连和音频上行前移到用户说话期间，但仍按一次语音段结束后提交模型响应，不具备真正连续对话的 turn detection 和插话事件桥。

本轮选择“方案二”：一次唤醒后进入连续对话窗口，由 Qwen Omni Realtime 的 `semantic_vad` 负责判断用户 turn，并以真实 `glass-esp32` 为最终落地点。

## 本轮变更

1. 新增设计文档 [Omni语义实时连续对话设计](../structure-design/Omni语义实时连续对话设计.md)，明确唤醒、接收、结束、等待、插话和嘈杂环境边界。
2. `ServerSettings` 新增连续对话配置：
   - `VOICE_CONVERSATION_MODE`
   - `VOICE_REALTIME_TURN_DETECTION`
   - `VOICE_REALTIME_SEMANTIC_VAD_THRESHOLD`
   - `VOICE_REALTIME_SILENCE_DURATION_MS`
   - `VOICE_REALTIME_PREFIX_PADDING_MS`
3. `voice.realtime.session.open` 的 `input` payload 新增 `conversation_mode` 和 `turn_detection`，让真实眼镜可以知道服务端期望的 turn detection 归属。
4. Omni Realtime 会话创建时按配置传入官方 SDK 的 turn detection 参数。
5. 补充单元测试，覆盖配置校验、实时语音 open payload 和 Omni 会话参数。
6. 更新 `local_server.env.example` 和业务开发指南，说明当前默认仍是稳定 `segment_turn`，`realtime_semantic_vad` 是实验模式。

## 当前边界

本轮是方案二第一阶段，不是完整生产级连续对话：

1. `VOICE_CONVERSATION_MODE=segment_turn` 仍是默认稳定模式。
2. `realtime_semantic_vad` 当前完成配置、协议和 Omni 会话参数接线，服务端连续事件桥和 `glass-esp32` 固件配合仍需后续迭代。
3. Omni Realtime 直出分支不执行 SDK Tool、Task、Skill、MCP；需要业务编排时仍应使用 `VOICE_REPLY_MODE=agent_tts`。
4. `semantic_vad` 不等于声纹识别，室外旁人说话仍需要真实眼镜端的近场拾音、AEC、VAD 阈值和退出策略配合。

## 后续计划

1. 在服务端增加 Omni 连续会话管理器，把 `speech_started`、`speech_stopped`、`response.audio.delta`、`response.done` 等事件转换为 SDK 内部事件。
2. 在播放中检测用户插话时调用 Omni `cancel_response`，并通过播放仲裁下发 `actuator.audio.interrupt`。
3. 为 `glass-esp32` 增加一次唤醒后的连续收音窗口、播放期间收音、按键退出、长静音退出和首包播放日志。
4. 为 `glass-playback` 增加多 turn 时间线回放，用于协议和回归验收。
