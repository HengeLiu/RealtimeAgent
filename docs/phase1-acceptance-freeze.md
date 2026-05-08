# audio-chat 第一阶段验收冻结记录

日期：2026-05-06

## 验收范围

本次只冻结第一阶段最小闭环，不进入第二阶段真实模型和真机能力。

已完成模块：

1. Control Service：设备注册、disabled/static token 鉴权、用户绑定、active device set、订阅声明、事件发布与分发。
2. Stream Service：stream 生命周期、`StreamChunk` 编解码、`sensor.mic` 输入和 `actuator.speaker` 输出传输。
3. Audio Pipeline：只处理 `sensor.mic`，完成格式校验、归一入口和路由，不做 server AEC。
4. TextAgentCore mock：固定 ASR transcript，输出 `assistant_text.delta`。
5. Output Service：`assistant_text.delta` 进入 mock Streaming TTS，生成 `assistant_audio.delta`，经 Playback Arbiter 下发 `actuator.speaker`。
6. Python playback endpoint：注册到 `user_id`、提交订阅、上报唤醒、打开 `sensor.mic`、接收 `actuator.speaker`、关闭音频会话。
7. 最小测试：协议契约、控制面订阅分发、音频链路、playback 回放。

## 架构边界审查

代码扫描命令：

```bash
rg -n "VoiceRuntime|DeviceGroupContext|MediaFrame|group_id|source_device_id|target_device_id" audio-chat-sdk tests app-examples docs -S
```

结果：

1. 旧概念只出现在 `docs/audio-chat-sdk-architecture.md` 的迁移背景和禁用说明中。
2. `audio-chat-sdk`、`tests`、`app-examples` 没有使用 `VoiceRuntime`、`DeviceGroupContext`、`MediaFrame`、`group_id` 或定向设备字段。
3. 第一阶段尚未实现 Tool / Task、MCP、Skill，因此不存在绕过 `UserDeviceContext` 协议原生 API 的业务调用路径。
4. 当前 server 不采集麦克风、不驱动喇叭、不控制端侧硬件；端侧只以 `device_id`、properties 和 subscriptions 注册。

## 测试命令和结果

```bash
uv run python -m pytest tests -q
```

结果：`8 passed`

```bash
git diff --check
```

结果：通过，无空白错误。

## Playback 配置

配置文件：

```text
app-examples/for-blind-app/host/glass-playback/sdk-playback.json
```

关键配置：

```json
{
  "runs_root": "runs/audio-chat",
  "user_id": "user-playback-001",
  "device_id": "dev-python-playback-001"
}
```

运行命令：

```bash
uv run audio-chat.playback.glass --config app-examples/for-blind-app/host/glass-playback/sdk-playback.json
```

结果：

1. `passed=true`
2. `output_chunk_count=1`
3. `output_bytes=2800`
4. 本次验收 session：`sess_6c1e43ea9c0b`

## 成功事件链

本次 playback 闭环确认的事件链：

1. `control.device.registered`
2. `control.user.wake.detected`
3. `control.audio_session.open.requested`
4. `control.audio_session.opened`
5. `stream.input.opened`
6. `agent.response.started`
7. `assistant_text.delta`
8. `assistant_audio.delta`
9. `stream.output.open.requested`
10. `stream.output.close.requested`
11. `stream.output.closed`
12. `control.audio_session.close.requested`
13. `control.audio_session.closed`

## 运行产物

本次产物位于：

```text
runs/audio-chat/sessions/sess_6c1e43ea9c0b/
```

已确认存在：

1. `runs/audio-chat/control-events.jsonl`
2. `runs/audio-chat/sessions/sess_6c1e43ea9c0b/events.jsonl`
3. `runs/audio-chat/sessions/sess_6c1e43ea9c0b/stream-events.jsonl`
4. `runs/audio-chat/sessions/sess_6c1e43ea9c0b/model-events.jsonl`
5. `runs/audio-chat/sessions/sess_6c1e43ea9c0b/input-stream_in_03c7aaab1f99.pcm`
6. `runs/audio-chat/sessions/sess_6c1e43ea9c0b/output-stream_out_2c28b98dd7ef.pcm`
7. `runs/audio-chat/sessions/sess_6c1e43ea9c0b/playback-result.json`
8. `runs/audio-chat/users/user-playback-001/messages.jsonl`

## 当前缺口

以下能力明确不进入第一阶段：

1. 真实 ASR provider。
2. 真实 Streaming TTS provider。
3. 真实文本模型 provider。
4. `RealtimeAudioAgentCore` / Qwen Omni Realtime。
5. ESP32 / Web / iOS 真机 endpoint。
6. server 侧噪声抑制、重采样、质量诊断 VAD。
7. `sensor.rgb`、`sensor.depth`、`sensor.imu` 的 Asset Service 完整实现。
8. Tool / Task、Skill Service、MCP Gateway、Memory Service。
9. 用户打断、队列恢复、跨任务抢播高级策略。

## 冻结结论

第一阶段最小闭环通过验收。当前代码边界与架构文档一致，可以作为第二阶段接入真实 ASR、真实 Streaming TTS、真实文本模型之前的冻结基线。
