# SDK 迭代记录：ESP32-S3 AEC 播放中自然插话

对应对外 SDK 版本：`sdk-v56`。

## 背景

`sdk-v55` 已经把 Omni `semantic_vad` 连续对话变成默认链路，真实 `glass-esp32` 可以在一次 WakeNet 命中后进入连续对话窗口。但播放期间仍保持半双工，原因是端侧没有把扬声器播放参考信号送入 AEC，继续收音会把助手自己的声音回灌给模型。

本轮针对 ESP32-S3 板子开启 AEC 试验链路，实现“播放中自然插话”的最小闭环。

## 本轮变更

1. `glass-esp32` 新增 `CONFIG_GLASS_ENABLE_AEC`，默认开启；新增 `CONFIG_GLASS_AEC_REFERENCE_BUFFER_MS` 控制播放参考环形缓冲。
2. AFE 输入格式在 AEC 开启时从 `M` 改为 `MR`，麦克风音频作为 `M`，扬声器下行 PCM 作为 `R` 参考通道。
3. 播放流写扬声器前同步把 mono PCM 写入 AEC 参考环形缓冲，语音前端每次 feed 时交错写入麦克风和参考音频。
4. 真实眼镜在 AEC 初始化成功时，上报 `accepted_mode=full_duplex_realtime`、`capabilities.aec=true`、`barge_in=true` 和 `output_cancel=true`。
5. 连续对话窗口有效时，播放期间保持 WakeNet/VAD 监听；检测到用户插话后，端侧先发送 `user.voice.interrupt`，再本地中断当前播放并开启新语音段。
6. 服务端播放仲裁补充迟到音频保护：旧 Omni/TTS 回复被插话打断后，即使后续仍有音频分片到达，也不会重新进入播放队列。
7. 开发指南和 Omni 连续对话设计文档更新到 `sdk-v56`。

## 验证

1. 单元测试新增“播放中插话后迟到旧回复音频不会重新入队”的覆盖。
2. 已运行 `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`，通过 26 个用例。
3. 本机当前没有可用 `idf.py`，ESP32 固件编译和真机 AEC 声学效果需要在 ESP-IDF 环境和 ESP32-S3 板子上继续验证。

## 风险和后续

1. AEC 打开不等于声学效果稳定，播放参考延迟、音量、扬声器和麦克风布局都会影响插话识别。
2. 当前服务端已经能中断旧播放并丢弃迟到音频，但上游 Omni response 主动取消还没有接到官方 SDK 能力，需要后续继续补齐。
3. 真机验收时重点观察：播放期间是否仍能触发用户语音段、旧播放是否立即停止、新语音段是否进入下一轮 Omni 回复，以及是否存在助手声音误触发。
