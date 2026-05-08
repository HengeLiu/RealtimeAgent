# audio-chat python-glass

`python-glass` 是用 Python 实现的参考眼镜端，定位等价于 `web-glass`：

1. 通过 `/ws/control` 注册设备、发送唤醒事件和回执事件。
2. 通过 `/ws/stream` 上传 `sensor.mic` 音频流。
3. 接收 `actuator.speaker` 下行音频并上报播放开始、完成和关闭。

它主要用于 server 和 Agent Core 的自动化回放测试，不依赖真实麦克风、浏览器权限或真人参与。

## 录制音频回放

```bash
uv run audio-chat.playback.glass \
  --server-url http://127.0.0.1:8765 \
  --audio-wav ../openaiglass-sdk/testdata/audio-sample/wav/看一下我前面有什么.wav
```

输入 WAV 必须是 16 kHz、单声道、16 bit PCM。endpoint 默认按 20 ms 切片上传，也就是每个 `sensor.mic` chunk 为 640 bytes。

