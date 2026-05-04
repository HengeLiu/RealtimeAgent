# sdk-v91 Omni 事件排障与非阻塞关闭

## 背景

`sdk-v90` 已经让 SDK 在收到 `response.audio.done` 后设置 Realtime 响应完成事件，但真机日志仍显示 `/stream.wav` 没有立即结束。日志顺序表明服务端已经打印 `Omni Realtime 音频输出完成`，随后仍等到 DashScope SDK 报 `request timeout after 23 seconds`，眼镜端才收到播放失败并恢复。

这说明新阻塞点不在音频完成事件识别，而在音频完成后同步调用 DashScope Realtime 会话 `close()`。底层 SDK 的关闭过程可能等待服务端响应或内部请求超时，导致 VoiceRuntime 还没来得及 finalize 播放流。

## 变更

1. Omni Realtime 主回复回调在 DEBUG 级别打印原始 server event 摘要：
   - 日志格式为 `Omni Realtime server event type=... payload=...`。
   - `response.audio.delta` 只打印 base64 长度，不打印完整音频内容。
   - 工具前置播报链路也打印同类事件摘要。
2. `OmniRealtimeStreamingSession.close(...)` 支持 `blocking=False`。
3. VoiceRuntime 在主回复完成后使用非阻塞关闭：
   - 先让 `finish(...)` 返回。
   - 先 finalize 下行播放流。
   - 再由后台线程关闭 DashScope Realtime 会话。
4. 被系统意图裁决忽略的预连接 Omni 会话也改为后台关闭，避免关闭动作影响 `voice.dialog.close` 下发。

## 验证

已执行：

```bash
uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_voice_runtime.py -q
```

结果通过。

新增单测覆盖：

1. 没有 `response.done`、只有 `response.audio.done` 时仍能正常返回。
2. DEBUG 日志中能看到 Omni server event 摘要。
3. `close(blocking=False)` 不等待底层 SDK close 完成。
