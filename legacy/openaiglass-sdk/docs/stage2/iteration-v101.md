# sdk-v101 播放子系统基础拆分

更新时间：2026-05-04

## 背景

Omni Server 与 Text Server 拆分进入运行时瘦身阶段后，`VoiceRuntime` 里仍然直接承载播放队列、播放仲裁结果落地、HTTP chunked WAV 输出、用户打断清理和通知播放流映射。播放链路同时影响普通回复、工具前置播报、通知播报、Task 终态播报和连续对话关闭，属于拆分中最需要先稳定的共享能力。

本轮先把播放流的本地队列和 HTTP 输出基础逻辑迁入独立模块，不改变三端协议和业务公开 API。

## 变更

1. 新增 `runtime/playback_streams.py`。
   - 收敛播放优先级排序、待播队列出队、播放流中断标记、按仲裁意图移除播放流、PCM 分片入队和播放完成哨兵写入。
   - 收敛播放流创建、统一播放仲裁器提交、高优先级播放打断和 `actuator.audio.play` 请求下发。
   - 收敛 `/stream.wav` 的 HTTP chunked WAV 响应头、分片写出、结束 chunk 和客户端断开处理。
2. `VoiceRuntime` 保留旧私有方法兼容入口。
   - `_create_playback_stream(...)`、`_enqueue_playback_chunk(...)`、`_finish_playback_stream(...)`、`_wait_for_playback(...)` 等测试和迁移期内部调用仍然可用。
   - 设备控制消息、通知协调器回调和播放完成后的连续对话关闭仍由 `VoiceRuntime` 编排。
3. package-check 增加 `runtime.playback_streams` 导入覆盖。

## 效果

`voice_runtime.py` 从 4358 行下降到 4241 行，播放基础逻辑新增独立模块 629 行。行数下降不是本轮唯一目标，重点是把播放状态操作从模型管线中剥离，为后续通知、Task 事件和 Omni/Text Server 进一步拆分提供稳定边界。

## 验证

已执行：

```bash
uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_task_event_runtime.py -q

uv run --python 3.11 --with pytest --with setuptools --with wheel \
  openaiglass.sdk.package-check --repo-root .
```

结果：播放相关单测 71 条通过，package-check 返回 `ok: true`。

## 对业务开发者的影响

业务代码不需要修改。`context.submit_notification(...)`、Tool `progress_message`、Task 终态通知和普通语音回复仍然进入同一套 `assistant.reply` / `actuator.audio.play` 播放链路。
