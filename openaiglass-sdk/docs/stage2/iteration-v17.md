# iteration-v17：全双工实时语音第一版

## 本轮目标

把全双工实时语音从设计文档推进到 SDK 第一版运行时能力，先覆盖协议事件、实时会话状态机、播放仲裁贯通、回声候选观测、迟到输出丢弃和回放级单测。

本轮对应对外 SDK 版本：`sdk-v18`。

## 主要改动

1. 新增 `RealtimeVoiceRuntime`，管理 `full_duplex_realtime` 会话、输入流、输出流、最近事件和延迟指标。
2. 新增 `RealtimeModelAdapter` 抽象，并提供 `LoopbackRealtimeModelAdapter` 与 `HalfDuplexFallbackRealtimeModelAdapter`，避免第一版强绑定模型供应商。
3. 现有 `VoiceRuntime` 持有实时语音运行时，并共享 `PlaybackArbiter`。
4. `/ws_realtime_audio` 作为实时媒体入口，继续复用 SDK `MediaFrame` 编码。
5. 控制面支持 `voice.realtime.session.open/opened/closed`、`voice.realtime.input.started/committed` 和 `voice.realtime.user_interrupt`。
6. 实时输出转换为 `PlaybackIntent(source=agent_reply)`，用户插话转换为播放仲裁器 `user_interrupt` 决策。
7. 用户插话后，SDK 下发 `actuator.audio.interrupt` 与 `voice.realtime.output.cancelled`，并丢弃同一输出流的迟到分片。
8. 运行态快照新增实时会话、输入输出流、打断、回声拒绝计数和延迟指标。

## 当前边界

1. 第一版采用 WebSocket `MediaFrame` 路径，不强制 WebRTC。
2. 服务端不实现声学 AEC/VAD 算法，只消费端侧结构化字段。
3. 真实实时模型供应商尚未绑定到默认运行时，后续通过 `RealtimeModelAdapter` 接入。
4. 半双工 `/ws_audio` 链路保持兼容，功能开发者不需要迁移已有业务能力。

## 验证结果

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py -q
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_voice_runtime.py openaiglass-sdk/tests/unit/test_playback_arbiter.py -q
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit -q
python -m compileall -q openaiglass-sdk/server-python
```

## 真机验收建议

1. 服务端启动后确认 `/api/runtime/devices` 中能看到 `realtime_state` 和 `active_realtime_session`。
2. 眼镜端或手机中继端连接 `/ws_realtime_audio`，发送 `voice.realtime.input.delta` 媒体帧。
3. 播放期间上报 `voice.realtime.user_interrupt`，确认服务端下发 `actuator.audio.interrupt` 和 `voice.realtime.output.cancelled`。
4. 注入 `voice_activity=echo` 或低置信度回声候选，确认 `realtime_echo_rejected_count` 增加且没有 `user_interrupt` 决策。
