# sdk-v97 语音模型服务边界抽象

更新时间：2026-05-04

## 背景

本轮开始按《Omni Server 与 Text Server 模态隔离设计》第 13 节分阶段实施。第一阶段目标是先把配置和代码概念里的模型服务边界立起来，避免继续用 `VOICE_REPLY_MODE` 同时表达“模型服务类型、输入模式、下行音频来源、连续对话策略”。

同时复核百炼 Omni Realtime 官方文档：Omni 链路应维护 Realtime WebSocket 长连接，持续追加音频，使用服务端 turn detection / `semantic_vad` 自动提交用户 turn；`response.audio.done` 用于收口当前音频输出，不应因此关闭连续对话模型连接。

## 变更

1. 新增 `ServerSettings.voice_server_mode`，支持 `omni_server` 和 `text_server`。
2. 新增环境变量 `VOICE_SERVER_MODE` 与 YAML 配置 `voice.server_mode`。
3. 保留旧 `VOICE_REPLY_MODE`，并做兼容映射：
   - `VOICE_REPLY_MODE=omni_realtime` -> `VOICE_SERVER_MODE=omni_server`
   - `VOICE_REPLY_MODE=agent_tts` -> `VOICE_SERVER_MODE=text_server`
4. `effective_voice_input_mode()` 改为基于有效 server mode 判断：
   - `omni_server` -> `raw_audio`
   - `text_server` -> `asr_text`
5. 运行时主分支改用 `effective_voice_server_mode()`，不再直接用旧 `voice_reply_mode` 判断 Omni/Text 热路径。
6. 新增内部 `VoiceServer` 协议和 `VoiceGateway`，作为后续 Phase 2/3 抽出 `OmniVoiceServer`、`TextVoiceServer` 的稳定入口。
7. 新增 `runtime.omni` 包入口，先导出当前 Omni Realtime 类型，后续把 DashScope Realtime 热路径逐步迁入该包。

## 对业务开发者的影响

业务 Tool、Task、Skill 不需要改。

新项目建议使用：

```yaml
voice:
  server_mode: omni_server
```

旧配置仍可继续使用 `VOICE_REPLY_MODE=omni_realtime` 或 `VOICE_REPLY_MODE=agent_tts`。如果同时配置新旧字段，必须保持一致；例如 `VOICE_SERVER_MODE=omni_server` 不能搭配 `VOICE_REPLY_MODE=agent_tts`。

## 后续阶段

1. Phase 2：把 `DashscopeOmniRealtimeReplyClient`、`OmniRealtimeStreamingSession` 和 Realtime tool bridge 迁入 `runtime/omni`。
2. Phase 3：把 ASR、文本意图、Text Agent、TTS 迁入 `runtime/text`。
3. Phase 4：废弃旧 `VOICE_REPLY_MODE` 内部主分支，只保留迁移兼容。

## 验证

```bash
uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_settings.py \
  openaiglass-sdk/tests/unit/test_server_cli.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py -q
```

结果：通过。
