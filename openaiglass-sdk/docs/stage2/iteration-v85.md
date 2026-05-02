# sdk-v85 真实眼镜连续 VAD 自循环修复

## 背景

2026-05-02 真机联调时，首次 WakeNet 唤醒后的天气问答可以正常完成，但播放结束后眼镜持续出现 `连续对话 VAD 触发新语音段`。服务端后续轮次的 `transcript_source=unavailable`，用户文本为空，却仍把自动抓拍图片交给 Omni Realtime，模型开始反复描述画面并继续下发播放，形成“空语音 + 自动抓拍 + 看图回复 + 再触发 VAD”的自循环。

同时，循环期间眼镜长时间处于播放和重新开段状态，用户再次呼叫“嗨乐鑫”时体验上表现为没有响应。

## 原因

1. 真实 ESP32 固件没有端侧 AEC，但收到服务端 `realtime_semantic_vad` 请求后仍保留 `semantic_continuous=1`。
2. 播放结束后，连续 VAD 只要满足短冷却和少量语音帧就会免唤醒启动下一段。
3. 服务端 Omni Realtime 字节流分支会在 `sensor.audio.segment.started` 时前置自动抓拍；即使最终没有 ASR 文本，图片仍可进入模型输入，导致模型把空段当成看图请求回答。

## 变更

1. ESP32 半双工降级时关闭免唤醒连续 VAD：
   - `voice.realtime.session.open` 仍可接收服务端的 realtime 请求。
   - 由于当前端侧声明 `aec=false`、`accepted_mode=half_duplex`，固件将 `s_realtime_semantic_dialog_enabled` 固定为 `false`。
   - 日志改为同时打印 `semantic_continuous_requested` 和 `semantic_continuous_enabled`。
   - 能力回报中的 `continuous_dialog=false`，`turn_detection_owner=endpoint`。
2. 语音段协议补充触发来源：
   - `sensor.audio.segment.started.payload.trigger` 为 `wake_word` 或 `continuous_vad`。
   - WakeNet 触发时保留 `wake_word` 详情；连续 VAD 触发时不再伪装成唤醒词触发。
3. 服务端增加连续 VAD 空段保护：
   - `SegmentBuffer.start_trigger` 记录端侧触发来源。
   - 对 `trigger=continuous_vad` 的语音段，服务端等待旁路 ASR。
   - 如果旁路 ASR 为空，则在进入 Omni Realtime 回复链路前抑制本轮，关闭预连接会话，并丢弃该段自动抓拍。
   - 该保护避免旧固件或自定义端侧仍误上报连续 VAD 时继续触发模型看图回复。

## 联调观察点

正常真机日志应看到：

```text
收到 voice.realtime.session.open，当前固件降级为半双工: session_id=... semantic_continuous_requested=1 semantic_continuous_enabled=0
WakeNet listening enabled for realtime-degraded session_id=...
```

一次问答播放结束后，不应继续自动出现：

```text
连续对话 VAD 触发新语音段
```

如果旧固件仍触发连续 VAD 空段，服务端应出现：

```text
已抑制连续 VAD 空语音段 segment_id=... input_stream_id=...
```

并且不应继续下发本轮 `assistant.reply` / `actuator.audio.play`。

## 验证

已执行：

```bash
uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_glass_esp32_runtime.py -q
uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_voice_runtime.py -q
uv run --with pytest python -m pytest openaiglass-sdk/tests/unit -q
```

结果：

1. ESP32 源码边界测试 3 条通过。
2. VoiceRuntime 单元测试 44 条通过。
3. SDK 全量 unit 通过。

## 设备级联调建议

本轮修复需要重新烧录真实 ESP32 固件后验证。联调顺序：

1. 同步配置：`uv run openaiglass.config.sync --app-root openaiglass-for-blind`。
2. 启动服务端并打开 `DEBUG` 日志。
3. 启动手机 App 或 `phone-mock`，确认设备绑定状态。
4. 烧录并启动新版 ESP32 眼镜。
5. 呼叫“嗨乐鑫”，说一句普通问题，例如“今天天气怎么样？”。
6. 等回复播放结束后保持安静 10 秒，观察眼镜不再自动开新段。
7. 再次呼叫“嗨乐鑫”，确认 WakeNet 仍能响应并开始新一轮。
