# web-glass reference endpoint

`web-glass` 是 audio-chat 的浏览器参考端侧，用于优先验证成熟 WebRTC AEC 链路。它不是 server SDK 核心逻辑，也不替代后续 ESP32-S3 固件。

## 设计要点

浏览器页面必须在同一个页面里完成麦克风采集和 server 下行音频播放：

```js
navigator.mediaDevices.getUserMedia({
  audio: {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  },
})
```

这样浏览器 AEC 才有真实远端播放参考。

## 启动

Mock / text 链路：

终端 1：

```bash
uv run audio-chat.server.run --config audio-chat/examples/minimal/server.yaml
```

Omni Realtime 链路：

```bash
DASHSCOPE_API_KEY=xxx uv run audio-chat.server.run \
  --config audio-chat/examples/minimal/server-omni.yaml
```

浏览器直接打开本地页面文件，例如在 macOS 上：

```bash
open audio-chat/endpoints-examples/web-glass/index.html
```

可选 query 参数：

```text
file:///.../audio-chat/endpoints-examples/web-glass/index.html?user_id=user-web&device_id=dev-web&server_url=http://127.0.0.1:8765
```

`web-glass` 是独立参考端侧，不由 `audio-chat.server.run` 通过 `/web-glass`
返回页面。server SDK 只暴露 `/ws/control`、`/ws/stream` 和 debug API；端侧类型只在设备
注册事件中声明。

## 手动验收

1. 点击“连接并注册”。
2. 点击“模拟唤醒”。
3. 授权浏览器麦克风。
4. 页面日志应打印 `mic settings`。
5. server `/api/debug/devices` 应看到 `client_type=web-glass`。
6. 页面应播放 `actuator.speaker` 下行音频。
7. 点击“结束”后应看到 `stream.input.closed` 和 `control.audio_session.closed`。

Omni Realtime 额外检查：

1. 页面不提供“提交本轮”按钮。
2. 麦克风打开后持续上传 `sensor.mic`，每片为 16 kHz PCM16 20ms，即 320 samples / 640 bytes。
3. 用户说完一句话后不点击提交，Omni provider 自己通过 turn detection / semantic VAD 触发回复。
4. server runs 中应看到 `omni.*` provider 事件和 `assistant_audio.delta`。
5. 下行音频为 provider native audio，不经过 TextAgentCore ASR 和 TTS。
6. 播放期间继续说话时，观察浏览器 WebRTC AEC 是否明显抑制 server 播放内容回灌。

## 当前状态

当前优先用 `web-glass` 验证浏览器 WebRTC AEC / NS / AGC 和全双工链路。页面已经按新 `StreamChunk` 协议持续上传音频，不使用旧 `MediaFrame`，也不依赖 `final:true` 触发回复。ESP32-S3 AEC 真机验收暂时后置，等 web-glass 链路稳定后再继续。
