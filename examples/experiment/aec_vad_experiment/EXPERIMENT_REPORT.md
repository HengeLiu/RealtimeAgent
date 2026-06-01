# iOS Voice Processing AEC/VAD 独立实验记录

## 背景

主 DeviceDemo 曾出现外放播放时被自身声音触发打断的问题。为了避免 server、Swift Device SDK、播放缓冲和打断逻辑互相干扰，本实验新建了一个独立 iOS App，只验证一件事：

> iOS `AVAudioEngine.inputNode.setVoiceProcessingEnabled(true)` 处理后的麦克风音频，是否仍会被独立 VAD 服务识别为用户说话。

## 实验方法

实验 App 不连接 `agent-server`，不依赖 `RealtimeAgentDeviceKit`。

端侧流程：

1. 配置 `AVAudioSession` 为 `.playAndRecord` + `.voiceChat`，输出默认走扬声器。
2. 按实验按钮开启或关闭 `engine.inputNode.setVoiceProcessingEnabled(...)`。
3. 安装 input tap，录制系统处理后的麦克风音频。
4. 用 `AVAudioPlayer` 外放内置测试音频 `testdata/audio-sample/自我介绍一下.wav`。
5. 录制结束后离线把 tap 捕获的 48k 音频降采样为 16k mono int16 WAV。
6. 上传 WAV 到独立 VAD 服务，判断是否触发语音。
7. 在手机端保留录音 WAV，并支持 `播放录音 WAV` 做主观检查。

服务端流程：

1. `server/vad_server.py` 接收 `POST /vad/analyze` 上传的 WAV。
2. 优先使用 `webrtcvad`，未安装时用 RMS 能量阈值兜底。
3. 返回 `triggered`、`speech_frames`、`total_frames`、`speech_ratio`、`first_speech_ms` 等指标。

启动命令：

```bash
python3 examples/aec_vad_experiment/server/vad_server.py --host 0.0.0.0 --port 8777
open examples/aec_vad_experiment/ios/AECVADExperiment.xcodeproj
```

## 排查过程

实验初版直接执行完整链路时出现 UI 卡住。为定位卡点，App 增加了单步探针，并把日志写入手机沙盒 `Documents/AECVADExperiment.log`。

单步验证结果：

```text
麦克风权限: permission=granted
配置音频会话: inputs[MicrophoneBuiltIn:iPhone 麦克风] outputs[Speaker:扬声器]
VoiceProcessing 开: voice_processing=on sample_rate=48000 channels=1
VoiceProcessing 关: voice_processing=off sample_rate=48000 channels=1
读取 input format: format sample_rate=48000 channels=1
启动 engine: engine=running sample_rate=48000 channels=1
```

这些结果说明：单独调用音频会话配置、Voice Processing 开关和 `engine.start()` 都不会卡住。

随后逐步移除了实验代码中的干扰项：

1. 去掉 `AVSpeechSynthesizer`，改为播放现成 WAV 样本。
2. 去掉实时 tap 回调里的 `AVAudioConverter + AVAudioFile.write`，tap 回调只复制 buffer 到内存。
3. 去掉离线阶段的 `AVAudioConverter + AVAudioFile`，改为普通 Swift 代码降采样并手写 WAV。

最终完整链路可跑完，并生成本地录音 WAV。

## 当前结果

一次有效测试日志：

```text
开始实验 voice_processing=true
配置 setVoiceProcessingEnabled
配置完成
加载内置测试音频
安装麦克风 tap
启动音频引擎
播放测试音并录制 8 秒
离线写入录音 WAV
离线转换 PCM buffers=88 input_rate=48000
离线写 WAV bytes=281600
离线 WAV 完成
上传 WAV 到 VAD
完成 wav=/var/mobile/Containers/Data/Application/.../Documents/aec-vad-vp-on-20260530-212758.wav
VAD triggered=false speech_frames=0/440 ratio=0.000 first_speech_ms=-
路由 inputs[MicrophoneBuiltIn:iPhone 麦克风] outputs[Speaker:扬声器]
```

录音 WAV 保存在手机 App 沙盒：

```text
Documents/aec-vad-vp-on-*.wav
```

## 结论

在独立实验 App 中，开启 Voice Processing 后，扬声器播放测试音频时录到的系统处理后麦克风音频没有触发独立 VAD：

```text
triggered=false
speech_frames=0/440
speech_ratio=0.000
```

因此，当前证据支持：

- iOS 系统 Voice Processing/AEC 在轻量独立路径中是有效的。
- 原主链路里的自我打断问题不应简单归因于“系统 AEC 完全无效”。
- 后续应重点对比主链路和本实验链路的差异，例如音频会话生命周期、播放方式、采集路径、是否复用同一个 engine、tap 回调负载、打断 VAD 的实际输入音频、以及服务端/端侧打断状态机。

## 注意事项

- 真机实际 input format 为 48k mono，即使请求 preferred sample rate 为 16k。
- 实验代码避免在 tap 回调中执行格式转换、文件写入或网络请求。
- `播放录音 WAV` 用于主观确认录音内容，但 VAD 判定仍以独立服务返回为准。
