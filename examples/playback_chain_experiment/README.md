# iOS 播放链路最小实验

本目录用于验证 Swift Device SDK 重写前的最小音频播放链路设计。实验不连接
`agent-server`，只使用独立脚本提供离线音频分片、真机 iOS App 播放、真机麦克风采集和
独立 VAD 服务。

正式设计见 [EXPERIMENT_DESIGN.md](EXPERIMENT_DESIGN.md)。

## 目录约定

```text
examples/playback_chain_experiment/
  EXPERIMENT_DESIGN.md
  README.md
  server/   # 离线音频分片服务和 VAD 分析服务
  ios/      # 真机 iOS 实验 App
```

## 实验目标

1. 验证 iOS 真机播放链路引入水位线 buffer 后，播放仍然稳定。
2. 验证 `AVAudioSession.playAndRecord + voiceChat + Voice Processing` 下，外放回采不会触发独立 VAD。
3. 验证模拟打断时，待播放 chunk、SDK buffer 和底层 renderer buffer 能被快速清理。

## 启动方式

启动离线音频分片服务：

```bash
python3 examples/playback_chain_experiment/server/audio_chunk_server.py \
  --audio testdata/audio-sample/自我介绍一下.wav \
  --host 0.0.0.0 \
  --port 8778 \
  --sample-rate 24000 \
  --chunk-ms 20
```

启动 VAD 服务：

```bash
python3 examples/playback_chain_experiment/server/vad_server.py \
  --host 0.0.0.0 \
  --port 8777 \
  --aggressive 2
```

使用百炼 Qwen-ASR-Realtime 的 server_vad：

```bash
export DASHSCOPE_API_KEY=sk-...
python3 examples/playback_chain_experiment/server/vad_server.py \
  --host 0.0.0.0 \
  --port 8777 \
  --backend dashscope \
  --dashscope-model qwen3-asr-flash-realtime \
  --dashscope-timeout-sec 30 \
  --dashscope-vad-threshold 0.0 \
  --dashscope-silence-duration-ms 400
```

`--dashscope-vad-threshold` 越高越不敏感。当前默认值先回到官方推荐的 `0.0`，用于确认真人插话能否
稳定触发；如果真人插话可触发但外放回采误触发，再按 `0.1`、`0.2` 逐档上调。

该服务同时提供两类 VAD/ASR 接口：

- `/vad/analyze`：接收完整 WAV，只保留为手动诊断接口；App 主流程不再自动调用。
- `/vad/realtime/sessions`：创建实时会话；iOS 会把麦克风 tap 中经过 Voice Processing/AEC
  之后的 16k PCM chunk 立即发到 `/chunks`，服务端持续转发给百炼 Realtime，并把
  `speech_started` / `speech_stopped` / ASR 文本作为事件返回。

实时打断只允许依赖 `/vad/realtime/sessions` 这一组接口，不能依赖最终整段 WAV 上传结果。
实验完成条件是播放链路结束并写入 `mic.wav`，不会等待完整 WAV 的离线 VAD/ASR。

iOS 工程位于 `ios/PlaybackChainExperiment.xcodeproj`。真机运行后，把 App 里的
Audio Server URL 和 VAD URL 改成 Mac 当前局域网地址，例如：

```text
http://192.168.10.10:8778
http://192.168.10.10:8777/vad/analyze
```

调试时优先使用 App 内的“复制日志”和“复制摘要”按钮，把水位线、chunk 拉取、打断清理、
实时 VAD/ASR 结果、WAV 和本地产物路径一起带回排查。D2 会在实时 `speech_started`
后触发 cancel；D3 会在 cancel 后继续录音，直到实时 `speech_stopped` 到达或等待超时。

## 本地检查

```bash
python3 -m py_compile \
  examples/playback_chain_experiment/server/audio_chunk_server.py \
  examples/playback_chain_experiment/server/vad_server.py

xcodebuild \
  -project examples/playback_chain_experiment/ios/PlaybackChainExperiment.xcodeproj \
  -scheme PlaybackChainExperiment \
  -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO \
  build
```
