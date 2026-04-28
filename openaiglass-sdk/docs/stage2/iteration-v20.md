# iteration-v20：SDK v21 glass-playback 控制循环修复

## 本轮目标

根据业务功能团队在 2026-04-28 的设备级回放反馈，修复 `glass-playback` 保存播放音频时阻塞控制消息循环的问题，并补齐最小设备级服务端产物断言。

本轮对应对外 SDK 版本：`sdk-v21`。

## 主要改动

1. `actuator.audio.play` 处理不再同步下载 `/stream.wav`。
2. `actuators.audio_play.save_audio_to` 改为后台线程保存，控制消息循环可以继续处理后续 `sensor.camera.capture`。
3. 保存成功、失败和调度都会写入事件日志，便于排查下行播放流保存问题。
4. `glass-playback` 配置新增 `assertions.server_artifacts`，可断言真实服务端业务产物文件已生成。
5. CLI 输出新增 `assertions_ok` 和 `assertion_failures`；断言失败时退出码为 `1`。

## 当前边界

1. 设备级断言当前只覆盖服务端文件产物存在性和最小大小，不做业务语义判断。
2. `{session_id}` 和 `{device_id}` 占位符只用于产物路径替换。
3. 更细的 Tool、Task、模型请求和业务结果语义断言仍由服务端回归工具或业务侧测试覆盖。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback:openaiglass-for-blind uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_playback_config.py -q

PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/phone-mock:openaiglass-sdk/glass-playback:openaiglass-for-blind uv run python -m compileall -q \
  openaiglass-sdk/server-python openaiglass-sdk/phone-mock openaiglass-sdk/glass-playback
```
