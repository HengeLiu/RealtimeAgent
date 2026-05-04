# sdk-v100 共享状态与音频工具拆分

更新时间：2026-05-04

## 背景

`sdk-v99` 已经把 Omni Realtime 客户端和 Text ASR/TTS 客户端从 `voice_runtime.py` 迁出。本轮继续处理剩余的大文件问题，先拆出不会改变运行行为的共享状态模型和音频工具，为后续拆播放、通知和进度播报缓存做准备。

## Phase 1-4 回顾

1. Phase 1 抽象边界：`sdk-v97` 已完成 `VoiceServer`、`VoiceGateway` 和 `voice.server_mode`。
2. Phase 2 抽出 Omni Server：`sdk-v98` 建立 Omni 适配器，`sdk-v99` 迁出 Omni Realtime 客户端；后续还要拆 Realtime tool bridge 和会话生命周期管理。
3. Phase 3 抽出 Text Server：`sdk-v98` 建立 Text 适配器和 TextDialogStateMachine，`sdk-v99` 迁出 ASR/TTS 客户端；后续还要拆 Text Agent Adapter。
4. Phase 4 清理旧分支：目前仍保留 `runtime.voice_runtime` 兼容导入和 `VOICE_REPLY_MODE` 映射，后续等物理边界更稳定后再收紧。

## 本轮变更

1. 新增 `runtime/audio_utils.py`。
   - 迁入 `PCM16StreamResampler`。
   - 迁入 `build_wav_bytes(...)` 和 `wav_header_unknown_size(...)`。
2. 新增 `runtime/voice_state.py`。
   - 迁入 `MessageEntry`、`VoiceTurnIntentDecision`、`SegmentBuffer`、`PlaybackStreamContext`、`VoiceSessionController`、`ReplySynthesisContext`、`ProgressAudioCacheEntry`。
3. `runtime.voice_runtime` 保留旧导入兼容。
   - 旧测试继续可以从 `runtime.voice_runtime` 导入这些类和函数。
4. package-check 增加 `runtime.voice_state` 和 `runtime.audio_utils` 导入验证。

## 效果

`voice_runtime.py` 从 `sdk-v99` 的 4663 行下降到 4385 行。剩余主体已经更清晰地集中在设备会话、播放、通知、Task 事件和模型管线编排。

## 下一步计划

1. 拆播放子系统：把 `PlaybackStreamContext` 之外的播放队列操作、chunked WAV 输出和中断清理辅助函数迁入播放模块。
2. 拆进度播报缓存：把工具前置播报缓存预热、读取、淘汰和 profile 判断收敛成独立 service。
3. 拆通知和 Task 事件：把通知直出、TaskEvent -> AgentTurn 回流和优先级处理从 `VoiceRuntime` 主体中移出。
4. 再收紧 `OmniVoiceServer` / `TextVoiceServer` 对具体子模块的直接拥有关系。

## 验证

```bash
uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_voice_server_boundaries.py -q
```

结果：通过，57 个测试通过。
