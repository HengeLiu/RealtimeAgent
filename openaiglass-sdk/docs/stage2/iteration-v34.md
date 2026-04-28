# iteration-v34：SDK v35 实时 ASR 热路径

## 本轮目标

把语音转写从“用户说完后提交整段 WAV”改为“用户说话时同步送入实时 ASR”，降低用户停止说话到 Agent 开始运行之间的等待时间。

本轮对应对外 SDK 版本：`sdk-v35`。

## 问题原因

旧链路中，`/ws_audio` 收到的 `audio_chunk` 只写入 `SegmentBuffer`。直到控制面收到 `sensor.audio.segment.finished`，`VoiceRuntime` 才把整段音频封装为 WAV 并通过非流式 Chat Completions ASR 请求转写。这样 ASR 的网络请求和模型处理全部发生在用户说完之后，无法达到 200ms 级首音频体验。

## 主要改动

1. 新增 `StreamingSpeechRecognitionSession` 抽象，支持音频帧到达时持续追加 PCM。
2. `DashscopeSpeechRecognitionClient` 在 `VOICE_ASR_MODE=realtime` 时创建百炼 Qwen ASR Realtime WebSocket 会话。
3. `VoiceRuntime.on_audio_frame(...)` 在缓存本地音频的同时，把每个 PCM 分片送入实时 ASR。
4. `VoiceRuntime._run_model_pipeline(...)` 优先读取实时 ASR 最终文本；实时 ASR 失败、超时或返回空文本时，自动回退原有整段 WAV ASR。
5. `config/local_server.env.example` 增加 `VOICE_ASR_MODE`、`VOICE_ASR_REALTIME_MODEL_NAME` 和 `VOICE_ASR_REALTIME_TIMEOUT_MS`。

## 当前边界

1. 这轮只解决 ASR 非流式问题。Agent 首 token、工具调用、视觉图片解读和 TTS 首音频仍可能成为后续瓶颈。
2. 当前 TTS 仍通过 CosyVoice 流式 WebSocket 边推文本边收音频；如果要继续压低首音频，需要接入实时 TTS 并减少按句提交等待。
3. 视觉问答会额外等待自动照片上传和多模态图片解读，不应拿视觉链路作为普通语音问答的最低延迟指标。
4. 实时 ASR 只在 16kHz、单声道、PCM16 输入下启用；其他输入会自动回退批量 ASR。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_server_cli.py -q
```
