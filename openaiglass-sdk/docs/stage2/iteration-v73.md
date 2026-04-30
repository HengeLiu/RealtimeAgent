# SDK 迭代记录：ESP32 播放任务创建可靠性

对应对外 SDK 版本：`sdk-v73`。

## 背景

真实眼镜连续对话时，服务端已经下发播放流，但 ESP32 可能打印 `创建 playback_stream_task 失败`。这表示播放任务没有创建出来，通常由内部堆可用连续块不足或任务栈分配失败导致。此前失败时端侧只写一条错误日志，没有向服务端回报播放失败，也没有输出堆内存诊断，容易导致状态排查困难。

## 本轮变更

1. 新增 `PLAYBACK_STREAM_TASK_STACK_SIZE` 常量，统一描述播放任务栈大小。
2. 播放任务从 `xTaskCreate` 改为 `xTaskCreateWithCaps(..., MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)`，优先把任务栈放入 PSRAM，减少内部堆压力。
3. 播放任务创建失败时打印内部堆和 PSRAM 的剩余量与最大连续块。
4. 播放任务创建失败时向服务端回报 `actuator.audio.state=failed`，原因是 `playback_task_create_failed`，并补发 `actuator.audio.finished`。
5. 失败清理时关闭扬声器通道、恢复本地监听状态，并给连续对话恢复加冷却，避免失败后立刻再次被 VAD 触发。
6. 更新 `SDK安装与能力开发指南.md` 和 `sdk-version`。

## 验证

1. `git diff --check`
2. `uv run openaiglass.glass.start --repo-root . --build-only`

本轮未执行真机烧录。真机验证时应重新烧录 ESP32 固件；如果仍出现播放任务创建失败，日志中的 `largest_internal` 与 `largest_spiram` 可以直接判断是否仍有堆碎片或 PSRAM 不足。
