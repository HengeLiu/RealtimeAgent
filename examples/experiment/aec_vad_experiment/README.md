# AEC/VAD 独立实验

这个目录是一个轻量真机实验，不连接 `agent-server`，不依赖 Swift Device SDK。

实验目的：在 iPhone 上开启或关闭 `AVAudioEngine.inputNode.setVoiceProcessingEnabled`，录制系统处理后的麦克风音频，同时用扬声器播放内置测试音频，再把录到的 WAV 发给独立 VAD 服务，观察外放回采是否仍会触发 VAD。

当前 App 内置播放样本来自：

```text
testdata/audio-sample/自我介绍一下.wav
```

详细实验过程和结论见 [EXPERIMENT_REPORT.md](EXPERIMENT_REPORT.md)。

## 启动 VAD 服务

```bash
python3 examples/aec_vad_experiment/server/vad_server.py --host 0.0.0.0 --port 8777
```

健康检查：

```bash
curl http://127.0.0.1:8777/health
```

如果手机和 Mac 在同一 Wi-Fi，App 里的默认地址是：

```text
http://192.168.10.10:8777/vad/analyze
```

如果 Mac IP 变化，把 App 首页的 `VAD URL` 改成当前地址。

## 打开 iOS 实验 App

```bash
open examples/aec_vad_experiment/ios/AECVADExperiment.xcodeproj
```

在 Xcode 里选择真机运行 `AECVADExperiment`。

## 测试方式

1. 先点 `VoiceProcessing 开`，等状态变成完成。
2. 再点 `VoiceProcessing 关`，用同样音量和手机位置复测。
3. 点 `复制日志`，把日志贴回来。
4. 重点对比 `VAD` 行里的 `triggered`、`speech_frames`、`ratio`、`first_speech_ms`。
5. 如需主观检查 AEC 后录音内容，点 `播放录音 WAV`。

预期观察：

- 如果 Voice Processing 有效，`开` 的 `speech_frames/ratio` 应显著低于 `关`。
- 如果 `开` 仍然稳定触发，说明系统回声抑制后的麦克风音频里仍有足够强的外放回采，需要继续看音频路由、音量、采样路径或系统模式。
