# SDK 迭代记录：工具前置播报缓存指纹校验

对应对外 SDK 版本：`sdk-v79`。

## 背景

工具前置播报支持启动时预生成 WAV 缓存，用于降低工具调用前提示音的首包延迟。此前缓存 key 只包含 TTS 模型、TTS 音色和播放格式，没有显式记录缓存是通过什么方式生成的，也不会在启动时清理旧版本缓存。

当最终回复切到 Omni Realtime 音频直出后，前置播报仍使用 CosyVoice TTS 缓存。即使这是当前实现的有意设计，也必须让缓存系统知道当前最终播报链路的模型和音色，否则切换 `VOICE_MODEL_VOICE`、`VOICE_OMNI_REALTIME_MODEL_NAME` 或后续切换前置播报生成方式时，旧缓存可能继续被复用。

## 本轮变更

1. 工具前置播报缓存新增 `.json` 元数据文件。
2. 缓存指纹新增以下字段：
   - `progress_audio_provider`
   - `tts_model_name`
   - `tts_voice`
   - `tts_sample_rate_hz`
   - `reply_audio_provider`
   - `reply_model_name`
   - `reply_voice`
   - `playback_sample_rate_hz`
   - `channels`
3. 启动预加载时会扫描缓存目录：
   - 没有元数据的旧 WAV 会被删除。
   - 元数据与当前配置不一致的缓存会被删除。
   - 不属于当前工具前置播报文案集合的缓存会被删除。
4. 删除后按当前配置重新生成缓存。

## 注意

当前默认实现仍是：

- 最终回复：`Omni Realtime`，由 `VOICE_OMNI_REALTIME_MODEL_NAME + VOICE_MODEL_VOICE` 控制。
- 工具前置播报：`CosyVoice TTS`，由 `TTS_MODEL_NAME + TTS_VOICE` 控制。

本轮解决的是“缓存是否与当前配置一致”的问题；如果要让前置播报和最终回复在声学模型层面完全一致，下一步还需要实现 Omni 生成前置播报缓存或统一把最终回复也切回 TTS。

## 验证

1. 单元测试：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q
```

结果：`73 passed`。

新增关键用例：

- `test_progress_audio_cache_prunes_stale_profile_on_startup`
  - 构造旧缓存 WAV 和旧元数据。
  - 启动 `VoiceRuntime` 后确认旧缓存被删除。
  - 确认新元数据包含当前最终播报链路的 provider、模型和音色。

2. 设备级回放：

```bash
LOG_LEVEL=DEBUG uv run openaiglass.server.start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env \
  --log-file openaiglass-for-blind/logs/server-progress-cache-profile-check.log

uv run openaiglass.phone.mock \
  --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json

uv run openaiglass.glass.start \
  --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/look_look.json
```

结果：`glass-playback` 返回 `assertions_ok=true`，`actuator_count=2`。

关键日志：

```text
2026-05-01T14:29:33.815189 server.voice-reply_a775b0422e74 工具前置播报命中静态音频缓存
2026-05-01T14:29:33.817884 server.voice-reply_a775b0422e74 下行播放请求已发送 audio_source=tts
2026-05-01T14:29:34.488136 server.voice Omni Realtime 返回首段音频
2026-05-01T14:29:36.128809 server.voice-reply_388fd383fc57 下行播放请求已发送 audio_source=omni_realtime
```

缓存元数据示例：

```text
progress_audio_provider=tts
reply_audio_provider=omni_realtime
reply_model_name=qwen3.5-omni-plus-realtime
reply_voice=Tina
tts_voice=longanhuan
```

验证结束后已停止本地 server 和 phone mock。
