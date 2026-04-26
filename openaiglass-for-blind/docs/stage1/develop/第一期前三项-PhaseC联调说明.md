# 第一期前三项 Phase C 联调说明

## 1. 目标

本说明用于验证 Phase C 的“真实语音对话闭环”是否打通，覆盖两类联调方式：

1. 本地模拟设备联调。
2. ESP32 真机联调。

验证通过标准：

1. 设备能在 WakeNet 命中后发送 `sensor.audio.segment.started`。
2. 设备能通过 `/ws_audio` 持续上行 `audio_chunk`。
3. 本地端点检测结束后，设备能发送 `sensor.audio.segment.finished`。
4. 服务端能调用模型并返回 `actuator.audio.play`。
5. 设备能拉取 `/stream.wav` 并回 `actuator.audio.started`、`actuator.audio.finished`。
6. 播放结束后设备恢复待命监听。

## 2. 服务端启动

推荐直接使用远程发布脚本，控制远端联调服务器：

```bash
export DASHSCOPE_API_KEY="<your-api-key>"
export DEVICE_TOKEN_MAP="glass-001=pair-demo-token"
export VOICE_ASR_MODEL_NAME="qwen3-asr-flash"
bash script/deprecated/run_server_phase_c_remote.sh all
```

如果需要在本机直接启动服务端，可在仓库根目录执行：

```bash
uv sync --python 3.11
export DASHSCOPE_API_KEY="<your-api-key>"
export DEVICE_TOKEN_MAP="glass-001=pair-demo-token"
export VOICE_ASR_MODEL_NAME="qwen3-asr-flash"
bash script/deprecated/run_server_phase_c.sh all
```

说明：

1. 不配置 `DASHSCOPE_API_KEY` 时，真实模型调用不可用。
2. 当前归档的 Phase C 服务端脚本默认监听 `8765`，日志文件默认是 `logs/server.log`。
3. 运行态观察接口：`http://127.0.0.1:8765/api/runtime/devices`
4. 远程脚本支持 `sync/start/stop/logs/all`，默认把当前仓库同步到 `ali5:/home/liuh/dev/<当前仓库名>`。
5. 本地 Python 相关命令统一使用 `uv + Python 3.11`。

## 3. 本地模拟设备联调

### 3.1 启动命令

服务端启动后，另开一个终端执行：

```bash
uv run --python 3.11 python script/phase_c_voice_client.py \
  --host 127.0.0.1 \
  --port 8765 \
  --device-id glass-001 \
  --pair-token pair-demo-token \
  --save-reply logs/phase_c_reply.wav
```

可选：

```bash
uv run --python 3.11 python script/phase_c_voice_client.py --wav /path/to/input.wav
```

要求：

1. 输入 WAV 必须是 `16kHz/mono/16bit`。
2. 不传 `--wav` 时，客户端会自动生成一段模拟 PCM。

### 3.2 预期客户端日志

应看到：

1. `recv: device.registered`
2. `recv: voice.session.open`
3. `recv: actuator.audio.play`
4. `downloaded reply wav bytes=...`
5. `round completed`

### 3.3 预期服务端日志

应看到：

1. `收到控制消息: sensor.audio.segment.started`
2. `收到控制消息: sensor.audio.segment.finished`
3. `ASR 转写结果: ...`
4. `输入大模型的 messages: ...`
5. `大模型文本回复: ...`
6. `已发送控制消息: actuator.audio.play`
7. `语音回复已准备: ...`

### 3.4 运行态观察

执行：

```bash
curl http://127.0.0.1:8765/api/runtime/devices
```

预期返回中：

1. `online_device_count=1`
2. `voice_sessions.glass-001.state` 最终回到 `listening`
3. `voice_sessions.glass-001.audio_connection_online=true`

## 4. ESP32 真机联调

### 4.1 烧录与监看

```bash
bash script/deprecated/run_glass_phase_c.sh -m
```

常用变体：

```bash
bash script/deprecated/run_glass_phase_c.sh --build-only
```

```bash
bash script/deprecated/run_glass_phase_c.sh --monitor-only -p /dev/cu.usbmodem2101
```

### 4.2 设备配置

沿用 Phase B 的 `Glass Runtime Config`：

1. WiFi 主/兜底配置。
2. `GLASS_SERVER_WS_URI` 指向服务端 `/ws/control`。
3. `GLASS_DEVICE_ID` 与服务端 `DEVICE_TOKEN_MAP` 键一致。
4. `GLASS_PAIR_TOKEN` 与服务端 `DEVICE_TOKEN_MAP` 值一致。

### 4.3 串口日志观察点

串口应看到：

1. `控制连接已建立`
2. `收到 voice.session.open`
3. `音频上行连接已建立`
4. `WakeNet detected: segment_id=...`
5. `已发送控制消息: sensor.audio.segment.started`
6. `已发送控制消息: sensor.audio.segment.finished`
7. `收到 actuator.audio.play: stream_id=...`
8. `已发送控制消息: actuator.audio.started`
9. `已发送控制消息: actuator.audio.finished`
10. `播放结束，恢复待命监听`

### 4.4 服务端日志观察点

服务端应同时看到：

1. `收到控制消息: sensor.audio.segment.started`
2. `收到控制消息: sensor.audio.segment.finished`
3. `ASR 转写结果`
4. `输入大模型的 messages`
5. `大模型文本回复`
6. `已发送控制消息: actuator.audio.play`
7. `语音回复已准备`

## 5. 验收步骤

### 5.1 正常对话一轮

1. 启动服务端。
2. 启动本地模拟客户端或烧录真机。
3. 确认 `voice.session.open` 到达。
4. 触发唤醒词或模拟音频上行。
5. 确认服务端收到 `segment.finished` 并下发 `actuator.audio.play`。
6. 确认设备完成播放并回 `actuator.audio.finished`。
7. 确认运行态最终回到 `listening`。

### 5.2 多轮连续对话

1. 完成一轮对话。
2. 再次触发唤醒词或再次运行模拟音频段。
3. 确认第二轮仍能完成 `started -> finished -> play -> started -> finished` 闭环。

### 5.3 异常验收

建议至少验证：

1. 配错 `pair_token` 后，设备不能进入语音态。
2. 不配置 `DASHSCOPE_API_KEY` 时，服务端日志中能明确报出模型配置缺失。
3. 播放期间设备不会再次触发 WakeNet。
4. 连续两轮提问时，第二轮 `messages` 中应包含第一轮用户文本历史。

## 6. 自动化测试命令

```bash
bash script/run_phase_c_tests.sh
```

覆盖范围：

1. Phase A/B 全量回归。
2. `24kHz -> 16kHz` 流式重采样。
3. `/ws_audio + segment.finished + actuator.audio.play + /stream.wav` 主链路集成测试。
