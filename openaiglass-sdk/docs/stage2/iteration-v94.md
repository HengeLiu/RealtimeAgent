# sdk-v94 ESP32 WakeNet SR 任务栈稳定性修复

更新时间：2026-05-04

## 背景

真机联调中，眼镜在呼叫唤醒词后出现：

```text
***ERROR*** A stack overflow in task sr_pipeline_tas has been detected.
```

崩溃发生在 ESP32 端 `sr_pipeline_task`。该任务原本只有 8KB 栈，但任务内同时持有预取音频环形缓冲；唤醒命中后还会同步播放本地提示音，扬声器恢复路径中也有较大的局部静音帧。两者叠加后，在 WakeNet 命中路径上容易触发 SR 任务栈溢出。

## 变更

1. 将 ESP32 端 SR 预取音频环形缓冲从 `sr_pipeline_task` 局部栈变量移到静态存储。
2. 将扬声器 I2S 恢复和播放启动使用的静音预装帧从局部栈变量移到静态常量。
3. 将 `sr_pipeline_task` 栈大小从 8KB 提升到 12KB，给 WakeNet、VAD、控制消息构造和本地提示音路径留出余量。
4. 不改变业务协议、不改变连续对话状态机、不改变 Tool/Task 扩展面。

## 对业务开发者的影响

业务能力代码不需要修改。本轮只修复真实 ESP32 眼镜端在唤醒词触发后的稳定性问题。

如果真机上仍看到 `唤醒提示音写入失败: ESP_ERR_TIMEOUT`，需要继续观察是否是扬声器 I2S 通道在上一次播放后未正常恢复；本轮已经先消除该路径上的栈溢出风险。

## 验证

已完成：

```bash
uv run openaiglass.glass.build --repo-root .
```

结果：ESP32-S3 固件编译通过，`glass_main.bin` 大小约 `0x16dc80`，分区剩余约 29%。

真机烧录验证暂被串口下载握手阻塞：

```text
Failed to connect to ESP32-S3: No serial data received.
```

需要将眼镜板进入下载模式后继续执行：

```bash
uv run openaiglass.glass.start --repo-root . --flash-only
uv run openaiglass.glass.start --repo-root . --monitor-only
```

真机验证重点：

1. 连续呼叫唤醒词 3 次，不再出现 `sr_pipeline_tas` 栈溢出。
2. 唤醒提示音正常播放，不出现或显著减少 `ESP_ERR_TIMEOUT`。
3. 首轮对话、连续追问、用户主动结束连续对话仍保持 `sdk-v92`/`sdk-v93` 的行为。
