# sdk-v108 Omni persistent 长连接忽略 turn 修复

## 背景

2026-05-05 真机联调日志显示，连续对话窗口仍在端侧保持，但服务端在 `semantic_vad_no_auto_response` 分支下发送 `voice.turn.ignored` 后，紧接着关闭了 Omni Realtime WebSocket。后续连续 VAD 新语音段只能重建模型连接，导致“有时无响应、有时误响应、保持安静后仍能继续对话”等状态混乱。

根因是服务端“忽略当前 turn”的实现和协议语义不一致：`voice.turn.ignored` 表示保留连续对话窗口，但 `_close_segment_without_reply(..., close_continuous_dialog=False)` 仍无条件关闭了当前 `segment.omni_realtime_session`。在 `voice_omni_session_lifecycle=persistent` 时，这个 session 正是 controller 上保存的长连接。

## 本轮改动

1. `OmniRealtimeStreamingSession` 新增 `discard_pending_input()`。
   - 调用 DashScope SDK 的 `clear_appended_audio()` 清理未提交 input buffer。
   - 只用于 semantic VAD 没有自动提交、SDK 决定忽略当前 turn 的场景。
   - 如果 SDK 不支持该方法或清理失败，抛出结构化异常交给上层重建连接。

2. `VoiceRuntime._close_segment_without_reply(...)` 区分两种收口：
   - `close_continuous_dialog=True`：关闭 Omni 会话并下发 `voice.dialog.close`。
   - `close_continuous_dialog=False` 且当前会话是 persistent Omni 长连接：只清理未提交输入并下发 `voice.turn.ignored`，不关闭 WebSocket。

3. 增加单元测试：
   - `voice.turn.ignored` 不下发 `voice.dialog.close`。
   - persistent Omni 会话在忽略当前 turn 时调用 `discard_pending_input()`，不会调用 `close()`，并继续保存在 controller 上。

## 对业务侧的影响

业务侧使用方式不变。连续对话仍由 SDK 和 Omni `semantic_vad` 管理：

- 普通追问应继续复用 persistent Omni 长连接。
- 用户明确说“结束对话”“保持安静”等停止指令时，模型工具或 SDK 停止指令识别会关闭连续窗口。
- 背景音、回声或 Omni 未自动提交的无效段只会被忽略，不应再破坏后续连续对话连接。

## 验证

已执行：

```bash
cd openaiglass-sdk/server-python
uv run --with pytest python -m pytest ../tests/unit/test_voice_runtime.py -k 'ignored_turn or omni_semantic_vad_without_auto_commit or persistent_omni'
uv run --with pytest python -m pytest ../tests/unit/test_voice_runtime.py ../tests/unit/test_realtime_voice_runtime.py ../tests/unit/test_settings.py
```

结果：

- 3 passed, 54 deselected
- 81 passed
