# audio-chat python-glass

`python-glass` 是用 Python 实现的参考眼镜端，定位等价于 `browser-glass`：

1. 通过 `/ws/control` 注册设备、发送唤醒事件和回执事件。
2. 通过 `/ws/stream` 上传 `sensor.mic` 音频流。
3. 接收 `actuator.speaker` 下行音频并上报播放开始、完成和关闭。

它主要用于 server 和 Agent Core 的自动化回放测试，不依赖真实麦克风、浏览器权限或真人参与。

## 录制音频回放

```bash
uv run audio-chat.playback.glass \
  --server-url http://127.0.0.1:8765 \
  --audio-wav legacy/openaiglass-sdk/testdata/audio-sample/wav/看一下我前面有什么.wav
```

输入 WAV 必须是 16 kHz、单声道、16 bit PCM。endpoint 默认按 20 ms 切片上传，也就是每个 `sensor.mic` chunk 为 640 bytes。

## Text 路线自动化

Text 模型路线优先使用本端侧做无头验收。测试中 mock ASR 会读取 `metadata.source_path`
中的 WAV 文件名作为转写文本，所以可以直接复用 `legacy/openaiglass-sdk/testdata/audio-sample/wav`
下的老样例，不需要额外维护文本脚本。

```bash
uv run python -m pytest tests/test_text_route_audio_samples.py -q
```

这组测试覆盖：

1. AudioSample 分片上传到 `sensor.mic`。
2. mock ASR 生成转写文本。
3. TextAgentCore 调用 `query_device_state` 或 `capture_photo`。
4. `capture_photo` 通过 `stream.control.configure.requested` 请求 `sensor.rgb`，再由 Python glass 上传资产。
5. 文本 delta 进入 Streaming TTS，并下发 `actuator.speaker`。
