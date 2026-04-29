# SDK v43 迭代记录

## 背景

2026-04-29 的回放链路日志显示，服务端在首个模型 token 后约 700ms 已下发 `actuator.audio.play`，但 `glass-playback` 端直接播放模式仍要等 `/stream.wav` 完整下载到临时文件后才调用播放器，导致本地听到声音明显滞后。同时 `actuator.audio.started` 在真正下载或播放前就已上报，状态语义不准确。

## 变更

1. `glass-playback` 的 `play_and_auto_finish` 模式优先使用支持 stdin 的播放器流式播放。
2. 未配置播放器且本机存在 `ffplay` 时，默认使用 `ffplay -nodisp -autoexit -loglevel error -i -`。
3. 配置 `player_command="ffplay ..."` 时，SDK 会自动补齐 `-i -`；显式包含 `{stdin}`、`-` 或 `pipe:0` 的命令会按配置使用。
4. 不支持 stdin 的播放器继续回退到“整段下载到临时文件后播放”，但会打印明确状态日志。
5. `play_and_auto_finish` 下的 `actuator.audio.started` 改为首段音频写入播放器后再上报。
6. 服务端新增 TTS 下行链路关键日志：`TTS 返回首段音频`、`下行播放请求已发送`、`播放流写出首段音频`。

## 业务影响

1. 业务回放配置如需直接听到下行语音，推荐安装 `ffplay` 并配置：

```json
"audio_play": {
  "mode": "play_and_auto_finish",
  "player_command": "ffplay -nodisp -autoexit -loglevel error"
}
```

2. 回放端 `actuator.audio.started` 不再表示“收到播放请求”，而表示“首段音频已经写入播放器”。
3. 排查下行语音延迟时，可以同时对比服务端和 glass-playback 的首包日志，区分 TTS、HTTP 播放流和本机播放器缓冲耗时。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_playback_config.py -q`
2. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
