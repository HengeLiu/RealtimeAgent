# sdk-v90 Omni 音频完成事件收口修复

## 背景

2026-05-04 真机联调发现，Omni Realtime 已经返回首段音频和完整助手文本，但服务端仍一直等待 `response.done`，导致 `/stream.wav` HTTP 流不结束。真实眼镜持续读取播放流，直到几十秒后读流失败，进而影响播放结束后的连续追问和再次唤醒。

对照阿里云百炼 Realtime server events 文档后，音频输出完成应优先使用 `response.audio.done`，`response.audio_transcript.done` 只代表音频转写文本完成，`response.done` 代表整体 response 对象完成。SDK 之前只把 `response.done` / `response.cancelled` 当作等待结束条件，事件使用过窄。

## 变更

1. Omni 主回复回调新增 `response.audio.done` 处理：
   - 没有待处理工具调用时，立即设置当前 Realtime 响应完成事件。
   - 播放流随后会 finalize，HTTP 下行流正常结束。
   - 如果当前响应正在执行工具调用，仍忽略旧响应的 audio done，避免工具首轮过早结束整轮。
2. 工具前置播报的 Omni Realtime 音频生成也支持 `response.audio.done` 收口，不再必须等待 `response.done`。
3. 保留 `response.done` / `response.cancelled` 兼容路径。
4. `response.audio_transcript.done` 继续只用于记录助手文本，不承担播放完成语义。

## 验证

已执行：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py \
  openaiglass-sdk/tests/integration/test_control_register_flow.py \
  -q
```

结果通过。保留既有 `PytestCollectionWarning`，不影响本轮修复。

本地 `openaiglass-for-blind/config/local_server.env` 没有配置 DashScope API Key，因此本轮未能在本机直接发起新的真实 Realtime 请求观测事件序列；修复依据来自官方 server events 文档、用户提供的真机日志和新增单元测试。
