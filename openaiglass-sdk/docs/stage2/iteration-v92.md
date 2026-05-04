# sdk-v92 Omni Realtime 长连接连续对话

更新时间：2026-05-04

## 背景

`sdk-v91` 解决了 `response.audio.done` 后同步关闭 DashScope Realtime 会话阻塞播放流的问题，但真机联调仍暴露出连续追问不稳定：普通每轮回复完成后模型连接被关闭，下一轮追问需要重新建连；如果 Omni `semantic_vad` 自动提交事件稍晚于端侧 `segment.finished`，SDK 会过早判定 `semantic_vad_no_auto_response` 并关闭连续窗口。

本轮按 [Omni Realtime 长连接连续对话重构设计](../structure-design/Omni-Realtime长连接连续对话重构设计.md) 落地第一阶段长连接能力。

## 变更

1. 新增 `VOICE_OMNI_SESSION_LIFECYCLE=per_turn|persistent` 配置，默认 `persistent`。
2. `VoiceSessionController` 持有设备级 `persistent_omni_realtime_session`，连续对话窗口内多轮语音复用同一条 Omni Realtime WebSocket。
3. `OmniRealtimeStreamingSession` 支持 `begin_turn(...)`：
   - 每轮重置响应事件、文本累积、response id、首包指标和工具计数。
   - 每轮刷新 instructions、tools、tool handler 和音频回调。
   - 普通 `response.audio.done` 只结束当前播放流，不关闭模型连接。
4. 用户主动结束、模型工具 `close_continuous_dialog`、端侧窗口关闭、控制连接关闭或不可恢复异常时，才后台关闭 persistent Omni 连接。
5. `semantic_vad` 未自动提交时增加短等待窗口，避免端侧 `segment.finished` 到达瞬间就误判关闭连续对话。
6. 运行态快照新增：
   - `omni_session_lifecycle`
   - `omni_persistent_connected`

## 对业务开发者的影响

1. 业务能力代码不需要修改。
2. 正常连续对话下，不应再看到每轮回复结束后都关闭 Omni Realtime WebSocket。
3. 如果真机联调需要回退旧行为，可在服务端配置中设置：

```env
VOICE_OMNI_SESSION_LIFECYCLE=per_turn
```

4. 仍然不要在业务侧自行关闭 Omni 连接；需要主动结束连续对话时，应让模型调用 SDK 内置 `close_continuous_dialog` 工具，或由端侧控制指令触发 `voice.dialog.close`。

## 验证

本轮代码级验证：

```bash
python -m py_compile openaiglass-sdk/server-python/runtime/voice_runtime.py openaiglass-sdk/server-python/infra/config/settings.py
uv run python -m unittest openaiglass-sdk.tests.unit.test_voice_runtime -v
```

真机验证应覆盖：

1. 一次唤醒后连续追问 3 轮，服务端只建立一条 persistent Omni 连接。
2. 每轮 `response.audio.done` 后眼镜播放流及时结束，但服务端不下发 `voice.dialog.close`。
3. 用户说“停下/安静/先这样”后，当前回复播报完成再下发 `voice.dialog.close` 并关闭 persistent Omni 连接。
4. DEBUG 日志中能看到每轮 `Omni Realtime server event type=...`，以及复用长连接时的 `Omni Realtime 长连接已刷新当前轮上下文`。
