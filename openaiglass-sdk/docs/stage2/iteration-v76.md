# SDK 迭代记录：Omni Realtime 上行字节流透传

对应对外 SDK 版本：`sdk-v76`。

## 背景

`sdk-v75` 已经让 Omni Realtime 直出链路重新接入 Agent-Core 和 SDK 工具，但真实日志仍显示一个关键问题：服务端会在 `sensor.audio.segment.finished` 之后才打开 Omni Realtime WebSocket，并把整段音频一次性追加给 Omni。这只能算“模型输出流式”，不是“麦克风输入到 Omni 的全链路字节流”。

本轮目标是让音频从端侧开始上传后，服务端立即把每个 PCM 分片转发给 Omni Realtime；语音段结束时只负责补图片、commit 和请求响应。

## 本轮变更

1. `AgentLoopRunner` 新增 `PreparedNativeAudioReply` 预备运行态，用于提前构造 Agent-Core 的系统提示词、工具 schema、工具处理器和调试请求摘要。
2. `AgentFacade` 新增原生音频轮次的准备与完成接口：
   - `prepare_native_audio_turn(...)`：语音段开始时准备 Agent-Core 上下文，不保存消息、不调用模型。
   - `complete_prepared_native_audio_turn(...)`：Omni 响应完成后，把用户转写、助手文本、工具轨迹、资产和模型请求摘要写回会话。
3. `VoiceRuntime` 在 `sensor.audio.segment.started` 时启动 Omni Realtime 会话，并创建 `ReplySynthesisContext`。
4. `/ws_audio` 每收到一段 `audio_chunk`，除写入本地 `SegmentBuffer` 外，也同步调用 `OmniRealtimeStreamingSession.append_audio(...)`。
5. 建连期间已经进入本地缓存的 PCM 会按顺序补推给 Omni，再切换到实时逐帧转发，避免丢帧或乱序。
6. `sensor.audio.segment.finished` 后复用已打开的 Omni 会话：
   - 等待本轮自动抓拍的短超时结果。
   - 按图片输入策略追加可直传图片。
   - 执行 `commit()` 和 `create_response(...)`。
   - 将 Omni 返回的音频 delta 继续写入同一条下行播放流。
7. 保留整段提交兜底：预连接失败、采样格式不支持或预推失败时，仍回退到旧的 segment-batch 路径。

## 验证

1. 单元测试：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q
```

结果：`69 passed`。

2. 设备级回放：

```bash
LOG_LEVEL=DEBUG uv run openaiglass.server.start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env \
  --log-file openaiglass-for-blind/logs/server-byte-stream-check.log

uv run openaiglass.phone.mock \
  --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json

uv run openaiglass.glass.start \
  --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/look_look.json
```

结果：`glass-playback` 返回 `assertions_ok=true`，`actuator_count=1`。

关键日志：

```text
2026-05-01T08:12:40.708639+00:00 glass-playback 开始发送触发音频 chunks=119
2026-05-01T08:12:41.064664+00:00 server.voice Omni Realtime 首段上行音频已推送 bytes=11520 frame_count=1
2026-05-01T08:12:41.064742+00:00 server.voice Omni Realtime 端到端输入流已启动 buffered_audio_bytes=11520 tool_count=6
2026-05-01T08:12:45.952571+00:00 glass-playback 触发音频发送完成 bytes=151552
2026-05-01T08:12:45.957739+00:00 server.voice Omni Realtime 请求已提交 audio_bytes=151552 audio_frame_count=111 image_count=1
2026-05-01T08:12:46.775006+00:00 server.voice Omni Realtime 返回首段音频 bytes=15360
```

这组日志确认：Omni 首段上行推送发生在端侧音频上传完成前约 4.9 秒，且最终提交时 `audio_frame_count=111`，不再是一整段音频一次性提交。

验证结束后已停止本地 server 和 phone mock。
