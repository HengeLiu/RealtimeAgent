# SDK v49 迭代记录

## 背景

`sdk-v47` 已经把 CosyVoice TTS 建连和流式任务启动前移，首个模型 token 到首段 TTS 音频的耗时主要剩在独立 TTS 服务首包。为了继续压低首听延迟，本轮新增 Qwen Omni Realtime 语音直出分支，让模型在同一次全模态调用中直接返回音频。

## 变更

1. 新增服务端配置：
   - `VOICE_REPLY_MODE=agent_tts|omni_realtime`
   - `VOICE_OMNI_REALTIME_MODEL_NAME`
   - `VOICE_OMNI_REALTIME_URL`
   - `VOICE_OMNI_PHOTO_WAIT_MS`
2. 默认 `agent_tts` 分支保持现有 ASR + agent-core + CosyVoice 流式 TTS，不删除任何已有链路。
3. 新增 `DashscopeOmniRealtimeReplyClient`，通过 DashScope `OmniRealtimeConversation`：
   - 关闭服务端 VAD，复用 SDK 当前语音段边界。
   - 发送本轮 16k PCM 音频和可选自动照片。
   - 监听 `response.audio.delta`，将模型音频分片直接写入现有播放流。
4. `VoiceRuntime` 在 `VOICE_REPLY_MODE=omni_realtime` 时绕过独立 ASR、agent-core 和独立 TTS，直接执行 Omni Realtime 语音直出。
5. 新增日志：
   - `Omni Realtime 请求已发送`
   - `Omni Realtime 返回首个文本`
   - `Omni Realtime 返回首段音频`
   - `Omni Realtime 最终回复`

## 边界

1. `omni_realtime` 当前用于低延迟普通问答和视觉问答，不执行 SDK Tool、Task、Skill 或长期记忆工具。
2. 需要导航、计时器、找物体、红绿灯等工具编排的能力时，应继续使用默认 `VOICE_REPLY_MODE=agent_tts`。
3. 本轮仍按半双工语音段边界提交 Omni 请求，没有把眼镜上行音频实时透传给 Omni Realtime。后续若要进一步降低 ASR/提交延迟，需要把 `/ws_audio` 的音频分片直接桥接到 Omni Realtime。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
3. 单测新增假 Omni Realtime 会话，验证 `response.audio.delta` 能直接转成播放音频分片。
