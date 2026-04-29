# SDK 迭代记录：glass-playback 本机麦克风输入

对应对外 SDK 版本：`sdk-v53`。

## 背景

`glass-playback` 原本只能通过 `sensors.trigger_audio.path` 回放固定 WAV 文件。稳定回归需要固定音频资产，但日常联调时开发者经常只想直接对着开发机麦克风说一句话，验证真实服务端、Omni/ASR、下行播放和设备事件链路。

## 本轮变更

1. `sensors.trigger_audio` 新增 `source` 字段：
   - `file`：默认值，继续读取 WAV 文件，保持原有回归语义。
   - `microphone`：采集开发机真实麦克风。
2. 麦克风模式支持配置：
   - `sample_rate_hz`
   - `channels`
   - `chunk_ms`
   - `duration_ms`
   - `device`
3. `glass-playback` 在麦克风模式下仍发送同一套真实眼镜协议：
   - `sensor.audio.segment.started`
   - `/ws_audio` 的 `MediaFrame(audio_chunk)`
   - `sensor.audio.segment.finished`
4. 命令行状态日志会标明 `source=microphone`、分片数、字节数和录音时长。

## 边界

麦克风模式是本地手动调试能力，不替代稳定自动化回归。它不做本机 VAD、唤醒词检测或自动停止，当前按 `duration_ms` 固定录音。正式验收仍应使用 WAV 文件资产，保证每次回放输入一致。

## 依赖

麦克风采集使用可选依赖 `sounddevice`。如果环境缺少该依赖，运行时会给出明确错误；开发者可执行 `uv pip install sounddevice`，macOS 如遇 PortAudio 问题可先执行 `brew install portaudio`。
