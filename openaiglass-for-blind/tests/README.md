# 业务能力自动化验收

当前最新实现的自动化验收分两层：

1. 业务单元测试：不启动三端设备，只验证 Tool、Task 对 SDK 公开能力的调用是否正确。
2. 设备级真机回放验收：使用 `glass-playback` 模拟真实眼镜，服务端连接真实 iPhone，不使用 `phone-mock`。

## 1. 单元测试

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind:. \
python3 -m unittest discover -s openaiglass-for-blind/tests -p 'test_*.py' -v
```

测试覆盖：

1. `start_timer` 输入校验、任务创建和 `timer_task` 生命周期。
2. `prepare_navigation` POI 确认、MCP 调用顺序、导航任务创建。
3. `navigation_task` 对红绿灯视觉事件的通知和去重。
4. `find_object_task`、`traffic_light_task` 对手机视频链路、手机任务和通知的调用。

这些测试只使用 SDK 的公开 `CapabilityResult`、`TaskContext` 和 `TaskEvent`，不替业务侧补 SDK 框架能力。

## 2. 真实 iPhone + glass-playback 验收

### 2.1 准备真实数据

把真实采集数据放到 `openaiglass-for-blind/testdata`：

```text
openaiglass-for-blind/testdata/audio/find_object_real_trigger.wav
openaiglass-for-blind/testdata/image/find_object_real_frame_001.jpg
```

要求：

1. 触发音频是 16-bit PCM WAV，默认 16000 Hz、单声道。
2. 图片帧或 MP4 视频应来自真实场景，不使用合成 mock 数据。
3. 如果改文件名，同步修改 `host/glass-playback/config/real_iphone_find_object.acceptance.example.json`。

### 2.2 准备服务端和真实 iPhone

`config/local_server.env` 至少要包含：

```bash
DEVICE_TOKEN_MAP="glass-playback-acceptance-001=pair-playback-acceptance,phone-001=pair-phone-token"
PHONE_DEVICE_ID="phone-001"
VOICE_SESSION_MODE="half_duplex"
LOG_LEVEL="DEBUG"
```

说明：

1. 这里的 `phone-001` 必须是真实 iPhone App 注册上来的设备，不要启动 `host/phone-mock`。
2. 当前 `glass-playback` 等待半双工 `voice.session.open`；如服务端使用全双工实时语音，应先切到 `VOICE_SESSION_MODE=half_duplex` 做回放验收。
3. 启动前先同步配置：

```bash
uv run openaiglass.config.sync --app-root openaiglass-for-blind
```

启动服务端：

```bash
uv run openaiglass.server.run --app-module host.server.main --app-root openaiglass-for-blind
```

启动真实 iPhone：

```bash
uv run openaiglass.phone.open --app-root openaiglass-for-blind
```

在 Xcode 中选择真实 iPhone，运行后确认 `phone-001` 已在线：

```bash
curl http://127.0.0.1:8765/api/runtime/devices
```

### 2.3 执行前置检查

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback:openaiglass-for-blind:. \
python3 openaiglass-for-blind/tests/glass_playback_acceptance.py \
  --config openaiglass-for-blind/host/glass-playback/config/real_iphone_find_object.acceptance.example.json \
  --phone-device-id phone-001 \
  --check-only
```

### 2.4 执行基础回放验收

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback:openaiglass-for-blind:. \
python3 openaiglass-for-blind/tests/glass_playback_acceptance.py \
  --config openaiglass-for-blind/host/glass-playback/config/real_iphone_find_object.acceptance.example.json \
  --phone-device-id phone-001
```

基础验收要求 `event_log` 至少出现：

1. `device.registered`
2. `voice.session.opened`
3. `device.binding.ready`
4. `voice.trigger_audio.started`
5. `voice.trigger_audio.finished`

### 2.5 执行找物体视频链路验收

SDK 补齐真实服务端中 `DeviceGroupContext.start_phone_video_link(...)` 标准装配后，再追加视频链路断言：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback:openaiglass-for-blind:. \
python3 openaiglass-for-blind/tests/glass_playback_acceptance.py \
  --config openaiglass-for-blind/host/glass-playback/config/real_iphone_find_object.acceptance.example.json \
  --phone-device-id phone-001 \
  --expect-event device.registered \
  --expect-event voice.session.opened \
  --expect-event device.binding.ready \
  --expect-event voice.trigger_audio.finished \
  --expect-event sensor.camera.stream.started \
  --expect-event sensor.camera.stream.finished \
  --expect-actuator actuator.audio.play
```

如果该命令失败，应优先看：

1. `runs/playback/glass-playback-acceptance-001/events.jsonl`
2. `runs/playback/glass-playback-acceptance-001/actuators.jsonl`
3. 服务端日志中的 `start_phone_video_link`、`sensor.camera.stream.start`、`phone.vision.find_object.result`
4. iPhone 日志中的视频帧接收和业务插件输出
