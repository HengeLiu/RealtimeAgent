# ESP32-S3 reference endpoint

本目录记录 audio-chat ESP32-S3 真机桥接参考端协议。当前仓库提供
`audio_chat_esp32_s3.esp32_aec` Python 契约模型和网络 smoke 参考实现，用于冻结
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
7. 收到 `sensor.rgb` 的 `stream.control.open.requested` 后，打开输入 stream
   上传 JPEG bytes；控制事件只保存 request_id、correlation_id、采样模式等语义字段。
8. 如果配置了 iOS phone 的直连相机接收地址，同时把同一帧 JPEG 按
   `audio_chat.direct_frame.v1` 格式推送到 `ws://<phone-ip>:9001/ws/camera`。

最小注册信息：

```json
{
  "client_type": "esp32-s3",
  "properties": {
    "audio.wake_word": "endpoint",
    "audio.aec": "endpoint",
    "audio.playback_reference": "endpoint_ring_buffer",
    "sensor.rgb.format": {"codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 1},
    "direct.camera_source": true,
    "direct.camera.frame_format": "audio_chat.direct_frame.v1"
  },
  "subscriptions": [
    {"event": "control.audio_session.*"},
    {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
    {"event": "stream.output.cancel.*", "filter": {"stream_type": "actuator.speaker"}},
    {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}
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
device_id、auth、音频格式、wake/AEC 模式、调试属性和订阅列表。真机固件可直接
按这些键读取配置，避免手写与 server 不一致的 device_id 或 token。

如果要联调 ESP32 到 iOS phone 的直连相机链路，先在 iOS phone 页面启动直连相机接收，
再把页面展示的 `ws://<phone-ip>:9001/ws/camera` 写入：

```bash
AUDIO_CHAT_PHONE_CAMERA_SINK_WS_URI=ws://192.168.1.50:9001/ws/camera
AUDIO_CHAT_PHONE_CAMERA_STREAM_INTERVAL_MS=500
```

直连只负责把相机帧送到 iOS phone 缓存；server 仍然通过 `stream.control.*` 请求
`sensor.rgb`，端侧再用 `/ws/stream` 上传进入对话的图片资产。

协议级验收：

```bash
uv run python -m pytest tests/test_esp32_s3_endpoint_contract.py tests/test_endpoint_config_sync.py -q
```

已有实验桥接说明见 [esp32-s3-endpoint-bridge.md](../../docs/esp32-s3-endpoint-bridge.md)。

## 参考固件工程

`firmware/` 目录提供最小 ESP-IDF 工程骨架，用于 package-check、dry-run build 和真机工程
迁移入口检查。它不是完整产品固件；真实硬件代码需要在该骨架上补齐 WiFi、WebSocket、
I2S、AEC、摄像头和串口诊断。

无 ESP-IDF 或无硬件时可以先做无副作用检查：

```bash
uv run audio-chat.esp32.build --dry-run --build-only
uv run audio-chat.esp32.monitor --dry-run --monitor-only --port /dev/tty.usbmodemXXXX
```

有 ESP-IDF 与真机时再执行：

```bash
uv run audio-chat.esp32.build
uv run audio-chat.esp32.flash --port /dev/tty.usbmodemXXXX
uv run audio-chat.esp32.monitor --port /dev/tty.usbmodemXXXX
```
