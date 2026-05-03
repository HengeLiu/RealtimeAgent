# sdk-v88 连续 VAD 空段收口修复

## 背景

2026-05-03 真机联调发现，服务端在 `sdk-v87` 中已经能抑制连续 VAD 空语音段，但抑制路径只关闭服务端的 Omni 预连接并返回，没有向眼镜端发送任何“本轮结束”控制消息。

因此眼镜端在 `sensor.audio.segment.finished` 后仍处于等待服务端回复状态，直到 `SERVER_REPLY_TIMEOUT_MS=45000` 超时才恢复待命，表现为“聊着聊着没响应”。

## 变更

1. `VoiceRuntime._should_suppress_empty_continuous_segment(...)` 在确认抑制空段时，改为复用 `_close_segment_without_reply(...)`。
2. 该路径会同步下发：

```text
voice.dialog.close
```

3. 眼镜端已有 `voice.dialog.close` 处理，会关闭连续对话窗口、重置连续 VAD 门控、调用 `clear_reply_wait_state()` 并恢复 WakeNet 待命。

## 验证

已执行：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_glass_esp32_runtime.py \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py \
  openaiglass-sdk/tests/integration/test_control_register_flow.py \
  -q
```

结果通过。保留一个既有 `PytestCollectionWarning`，不影响本轮修复。

关键单测已更新：连续 VAD 空段被抑制时必须包含 `voice.dialog.close`，避免端侧等待回复超时。
