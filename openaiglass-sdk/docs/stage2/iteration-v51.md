# SDK 迭代记录：语音输入模式与下行音频日志口径

对应对外 SDK 版本：`sdk-v51`。

## 背景

`sdk-v49` 引入 `VOICE_REPLY_MODE=omni_realtime` 后，服务端已经可以绕过独立 ASR、agent-core 和 CosyVoice TTS，把语音与自动照片直接提交给 Qwen Omni Realtime。但实际联调日志暴露两个问题：

1. 共用播放流日志仍写成 `TTS 返回首段音频`，在 Omni Realtime 分支会误导排障。
2. 是否启用独立 ASR 只隐含在 `VOICE_REPLY_MODE` 中，缺少显式配置入口。

## 本轮变更

1. 新增 `VOICE_INPUT_MODE=auto|asr_text|raw_audio`：
   - `auto` 为默认值。
   - `VOICE_REPLY_MODE=agent_tts` 时实际等价于 `asr_text`。
   - `VOICE_REPLY_MODE=omni_realtime` 时实际等价于 `raw_audio`。
2. 增加配置校验：
   - `agent_tts + raw_audio` 会阻止启动，因为当前 Agent + TTS 分支需要文本输入。
   - `omni_realtime + asr_text` 会阻止启动，因为当前 Omni Realtime 分支直接消费原始音频。
3. `VoiceRuntime.on_segment_started(...)` 改为按实际语音输入模式决定是否启动实时 ASR。
4. 语音链路起始日志新增 `voice_input_mode`、`reply_mode`、`reply_model`，并在跳过独立 ASR 时显示 `asr_model=<skipped>`。
5. 共用播放层首包日志改为 `下行音频源返回首段音频`，并携带 `audio_source=tts|omni_realtime`。
6. `下行播放请求已发送` 和 `播放流写出首段音频` 日志改用 `source_audio_to_*` 字段，不再写死为 `tts_audio_to_*`。

## 使用说明

默认配置不需要修改：

```env
VOICE_REPLY_MODE="agent_tts"
VOICE_INPUT_MODE="auto"
```

低延迟 Omni Realtime 直出：

```env
VOICE_REPLY_MODE="omni_realtime"
VOICE_INPUT_MODE="auto"
VOICE_OMNI_REALTIME_MODEL_NAME="qwen3.5-omni-plus-realtime"
```

如果当前模型不支持语音输入，应使用 Agent + TTS 分支：

```env
VOICE_REPLY_MODE="agent_tts"
VOICE_INPUT_MODE="asr_text"
```

## 边界

`VOICE_INPUT_MODE` 目前只控制服务端是否启动独立 ASR。`omni_realtime` 分支仍会使用 Omni Realtime 的原始音频输入能力，并通过模型返回的转写文本记录本轮 transcript；它不会执行 SDK Tool、Task、Skill 或长期记忆工具。
