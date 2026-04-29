# iteration-v37：SDK v38 glass-playback 下行语音直接播放

## 本轮目标

让功能开发者在使用 `glass-playback` 做设备级回放时，可以直接听到服务端下行语音，而不是只能把音频保存到文件后再手动打开。

本轮对应对外 SDK 版本：`sdk-v38`。

## 主要改动

1. `actuators.audio_play.mode` 新增 `play_and_auto_finish`。
2. 新模式会从服务端 `/stream.wav` 下载下行语音到系统临时文件，调用本机播放器播出，播放结束后删除临时文件。
3. 新模式会在收到播放命令时上报 `actuator.audio.started`，并在播放线程结束后上报 `actuator.audio.finished`。
4. 默认播放器选择：macOS 使用 `afplay`；Linux 依次尝试 `paplay`、`aplay`、`ffplay`；配置 `audio_play.player_command` 时优先使用业务指定命令。

## 当前边界

1. 直接播放模式不会写入 `save_audio_to` 目录；需要留存音频用于断言或回溯时仍应使用 `record_and_auto_finish`。
2. 当前实现为“下载到临时文件后播放”，不是边下载边播放的低延迟播放器。
3. 找不到本机播放器时只记录 `actuator.audio.play_failed` 事件，并仍会上报播放结束，避免服务端等待执行器状态。

## 验证

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback:openaiglass-for-blind \
uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_playback_config.py -q
```
