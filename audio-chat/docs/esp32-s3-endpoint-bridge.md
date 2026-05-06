# ESP32-S3 Endpoint Bridge 联调说明

日期：2026-05-06

## 当前状态

Phase 2.5 已完成 server 侧协议、provider 和 playback 验收，但本轮没有连接物理 ESP32-S3。因此本文件记录最小真机联调入口、事件检查点和 AEC reference 写入要求；真机日志尚未产生，不能把 ESP32 真机能力描述为已完成。

2026-05-07 更新：ESP32-S3 真机 AEC 验收暂时后置。下一阶段优先使用 `web-glass` 浏览器参考端侧验证全双工语音链路，因为浏览器 WebRTC AEC 能在同一页面拿到真实麦克风输入和 server 下行播放参考。ESP32-S3 bridge 文档保留，等 `web-glass` 链路稳定后继续。

可复用的旧试验代码：

1. `openaiglass-sdk/glass-esp32/main/test_official_aec.c`
2. `openaiglass-sdk/server-python/devtools/omni_esp32_aec_relay.py`

旧试验代码里的关键经验需要迁移到新版 audio-chat endpoint bridge：

1. AEC reference ring buffer。
2. mic send queue。
3. playback ring buffer。
4. 下行 speaker audio 先进入端侧播放缓冲。
5. 端侧实际写 I2S 播放同一帧音频时，同步写入 AEC reference ring buffer。

## Server 启动

先使用 mock playback 配置确认 server SDK 基线：

```bash
uv run audio-chat.dev.preflight --report runs/audio-chat/preflight-phase25.json
uv run audio-chat.playback.glass --config audio-chat/examples/minimal/playback.yaml
```

接真机时应使用 server YAML 配置作为入口，并把日志级别调到 DEBUG：

```bash
LOG_LEVEL=DEBUG uv run audio-chat.server.run \
  --config audio-chat/examples/minimal/server.yaml
```

如果当前 CLI 尚未接入长期运行 server 命令，应先使用 playback endpoint 或后续最小 bridge server 承载同一套 Control Service、Stream Service、TextAgentCore 和 Output Service，不允许回退到旧 `MediaFrame` 公开协议。

## ESP32 端启动与刷写

ESP32-S3 端最小 bridge 固件需要声明：

```json
{
  "device_id": "dev-esp32-glass-001",
  "client_type": "esp32-glass",
  "capabilities": {
    "audio.aec": "endpoint",
    "streams.produce": ["sensor.mic"],
    "streams.consume": ["actuator.speaker"],
    "wake": true
  },
  "subscriptions": [
    "control.audio_session.open.requested",
    "control.audio_session.close.requested",
    "stream.input.open.requested",
    "stream.output.open.requested",
    "stream.output.close.requested"
  ]
}
```

旧 AEC 试验固件刷写入口仍以 `openaiglass-sdk/glass-esp32` 为准；迁移到 audio-chat bridge 后，刷写前需要在 Kconfig 或本地配置里写入 server WebSocket 地址、WiFi 和设备 token。真实命令应在真机联调时补入本文件，避免提交本地 WiFi 或 token。

## 成功事件链

真机验收必须在 `runs/audio-chat/...` 和 ESP32 串口日志中看到以下链路：

1. `control.device.registered`
2. `control.user.wake.detected`
3. `control.audio_session.open.requested`
4. `control.audio_session.opened`
5. `stream.input.opened`，`stream_type=sensor.mic`
6. `sensor.mic` chunk 持续上传，格式为协商后的 PCM16。
7. `agent.response.started`
8. `assistant_text.delta`
9. `assistant_audio.delta`
10. `stream.output.open.requested`，`stream_type=actuator.speaker`
11. ESP32 收到 speaker chunk 后上报 `stream.output.started`
12. server 请求关闭 output 后，ESP32 上报 `stream.output.finished` 和 `stream.output.closed`
13. 连续对话结束后看到 `stream.input.closed` 和 `control.audio_session.closed`

## 音频连接生命周期

ESP32 端不应在启动后保持 24 小时 `sensor.mic` 常驻上传。最小规则：

1. 启动后只注册、鉴权、提交订阅。
2. 本地 wake word 命中后上报 `control.user.wake.detected`。
3. 收到 audio session open / stream open 后才上传 `sensor.mic`。
4. 连续对话结束、server 关闭会话或端侧超时后关闭 `sensor.mic`。
5. 端侧播放失败时上报 `stream.output.failed`，不要静默丢 chunk。

## AEC Reference 观察点

ESP32 端应输出以下 DEBUG 统计：

```text
speaker_chunks_received=<n>
playback_ring_bytes=<n>
aec_reference_bytes=<n>
mic_chunks_sent=<n>
aec_output_bytes=<n>
```

验收条件：

1. `speaker_chunks_received` 增长后，`playback_ring_bytes` 应同步增长。
2. 实际写播放器时，`aec_reference_bytes` 应增长，且来源是同一帧 speaker PCM。
3. `mic_chunks_sent` 上传的是端侧 AEC 后音频。
4. server 只记录 `audio.aec=endpoint` 和质量诊断，不声称 server 实现 AEC。

## 本轮真机结果

本轮未连接物理 ESP32-S3，因此以下项目仍为阻塞：

1. 注册成功事件的真机日志。
2. wake 事件的真机日志。
3. `sensor.mic` opened/chunk/closed 的真机日志。
4. `actuator.speaker` open/chunk/started/finished/closed 的真机日志。
5. AEC reference 写入统计。

完成真机联调后，应把 server 启动命令、固件刷写命令、串口日志路径、runs 目录路径和失败点补回 `phase2-acceptance-record.md`。
