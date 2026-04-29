# SDK v44 迭代记录

## 背景

修复 `glass-playback` 下行语音播放后，需要确认真实 ESP32 眼镜是否存在同类问题。代码检查显示，ESP32 固件当前不是“整段下载后播放”：它在收到 `actuator.audio.play` 后启动 `playback_stream_task`，打开 `/stream.wav` HTTP 流，读取 44 字节 WAV 头后按约 20ms 的 PCM 分片写入 I2S，并在首次写入扬声器后才上报 `actuator.audio.started`。

## 变更

1. 在 ESP32 固件播放入口增加 `准备启动播放流` 日志，打印 `stream_id` 和 `/stream.wav` 地址。
2. 在播放任务中增加 `播放流 HTTP 已打开` 日志，记录收到播放请求到 HTTP 打开的耗时。
3. 增加 `播放流 WAV 头已读取` 日志，记录收到播放请求到 WAV 头完成的耗时。
4. 增加 `播放流收到首段 PCM` 日志，记录首段真实 PCM 到达设备的耗时。
5. 增加 `播放流首段音频已写入扬声器` 日志，记录首段音频写入 I2S 的耗时。

## 结论

真实 ESP32 眼镜没有发现 `glass-playback` 原先那种“先完整下载再播放”的结构性问题。后续如果真机听感仍然延迟明显，应把 ESP32 日志和服务端 `TTS 返回首段音频`、`下行播放请求已发送`、`播放流写出首段音频` 对齐，定位延迟是在服务端 TTS、HTTP 首包、网络读取、I2S 写入还是功放实际出声阶段。

## 验证

1. `git diff --check -- openaiglass-sdk/glass-esp32/main/glass_main.c`

本机当前没有 `idf.py`，未执行 ESP-IDF 固件编译；需要在已安装 ESP-IDF 的环境中用 `uv run openaiglass.glass.start --app-root openaiglass-for-blind --sdk-root openaiglass-sdk --port '<串口>'` 完成构建、烧录和串口日志验证。
