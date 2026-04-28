# iteration-v25：SDK v26 绑定等待诊断

## 本轮目标

根据 2026-04-28 的联调日志反馈，补齐 `glass-playback` 在发送触发音频前卡住时的诊断信息。用户看到服务端只有 voice session 日志但没有 ASR/Agent 日志时，应能直接判断是否还没进入音频链路。

本轮对应对外 SDK 版本：`sdk-v26`。

## 主要改动

1. 服务端自动绑定未满足条件时打印 INFO 日志，包含当前在线 glass、在线 phone、期望绑定设备和提示。
2. `glass-playback` 等待目标 phone 绑定前打印 `等待设备绑定`。
3. `glass-playback` 开始发送触发音频前打印流编号、音频段编号、音频路径和 chunk 数。
4. `glass-playback` 触发音频发送完成后打印完成状态。
5. `glass-playback` 运行失败时打印失败原因并写入事件日志。

## 当前边界

1. 如果配置了 `desired_phone_device_id` 且 `startup.wait_for_binding=true`，`glass-playback` 会等到目标 phone 在线并绑定后才发送触发音频。
2. 服务端没有 `sensor.audio.segment.started` 日志时，说明还没有进入 ASR/Agent 链路。
3. 如果只验证普通语音问答，不依赖手机能力，可以在回放配置中移除 `desired_phone_device_id` 或设置 `startup.wait_for_binding=false`。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_playback_config.py \
  openaiglass-sdk/tests/integration/test_control_register_flow.py -q
```
