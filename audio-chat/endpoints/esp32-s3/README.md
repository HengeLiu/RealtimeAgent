# ESP32-S3 reference endpoint

本目录记录 audio-chat ESP32-S3 真机桥接参考端协议。当前仓库提供
`audio_chat.endpoints.esp32_aec` Python 契约模型和网络 smoke 参考实现，用于冻结
注册、会话、stream 和 AEC 诊断语义；物理 ESP32-S3 固件仍需要在硬件环境中按同一协议
实现和验收。

端侧职责边界：

1. 启动后只建立 `/ws/control`，发送 `control.device.register.requested`。
2. 本地唤醒词命中后发送 `control.user.wake.detected`。
3. 收到 `control.audio_session.open.requested` 后回传
   `control.audio_session.opened`，再打开 `/ws/stream` 上传 `sensor.mic`。
4. 连续对话关闭、server 请求关闭或端侧超时后，发送 `stream.input.closed` 并释放
   `sensor.mic` stream。
5. AEC、NS、AGC、麦克风采样、I2S 播放和硬件时钟都在端侧完成。
6. 下行 `actuator.speaker` stream 播放开始后上报 `stream.output.started`；播放完成后
   上报 `stream.output.finished` 和 `stream.output.closed`；用户打断时上报
   `control.user.interrupt.detected` 和对应 output cancel 回执。

最小注册能力：

```json
{
  "client_type": "esp32-s3",
  "capabilities": {
    "streams.produce": ["sensor.mic"],
    "streams.consume": ["actuator.speaker"],
    "audio.wake_word": "endpoint",
    "audio.aec": "endpoint",
    "audio.playback_reference": "endpoint_ring_buffer"
  },
  "subscriptions": [
    {"event": "control.audio_session.*"},
    {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
    {"event": "stream.output.cancel.*", "filter": {"stream_type": "actuator.speaker"}}
  ]
}
```

本地配置由以下命令生成：

```bash
uv run audio-chat.config.sync \
  --server-url http://127.0.0.1:8765 \
  --user-id user-endpoint-001
```

生成的 `esp32-s3.local.env` 至少包含 server URL、control/stream WebSocket URL、user_id、
device_id、auth、音频格式、wake/AEC 模式、stream capability 和订阅列表。真机固件可直接
按这些键读取配置，避免手写与 server 不一致的 device_id 或 token。

协议级验收：

```bash
uv run python -m pytest tests/test_esp32_s3_endpoint_contract.py tests/test_endpoint_config_sync.py -q
```

已有实验桥接说明见 [esp32-s3-endpoint-bridge.md](../../docs/esp32-s3-endpoint-bridge.md)。
