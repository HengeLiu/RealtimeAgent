# glass-playback

`glass-playback` 是设备级虚拟眼镜运行时，与 `server-python`、`phone-ios`、`glass-esp32` 同级。

它不是服务端 SDK 子模块，也不是组件级测试 runner。它按真实 glass 设备协议连接服务端，完成注册、心跳、语音会话、触发音频上行、抓拍回传、视频帧推送和执行器记录。

统一启动入口仍由 SDK CLI 提供：

```bash
openaiglass.glass.start --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/glass.water_cup.json \
  --repo-root .
```

配置文件放在业务工程 `openaiglass-for-blind/host/glass-playback/config`，音频、图片、视频和传感器资产放在 `openaiglass-for-blind/testdata`。

`sensors.trigger_audio` 默认使用 WAV 文件；本地手动调试时也可以配置 `source: "microphone"` 采集开发机真实麦克风。麦克风模式需要可选依赖 `sounddevice`，并按固定 `duration_ms` 录音，不做本机 VAD 或唤醒词检测。

需要连续验证多轮语音时，可以额外配置 `sensors.trigger_audio_sequence`。回放进程会先提交队列第一条音频，等服务端回复音频播放完成并上报 `actuator.audio.finished` 后，再提交下一条，如此循环直到队列耗尽。保留 `sensors.trigger_audio` 是为了兼容旧配置。

```json
{
  "sensors": {
    "trigger_audio": {
      "path": "testdata/audio/看一下我前面有什么.wav",
      "format": "wav"
    },
    "trigger_audio_sequence": [
      {
        "path": "testdata/audio/看一下我前面有什么.wav",
        "format": "wav",
        "sample_rate_hz": 16000,
        "channels": 1,
        "chunk_ms": 40
      },
      {
        "path": "testdata/audio/我叫什么呀.wav",
        "format": "wav",
        "sample_rate_hz": 16000,
        "channels": 1,
        "chunk_ms": 40
      }
    ]
  }
}
```
