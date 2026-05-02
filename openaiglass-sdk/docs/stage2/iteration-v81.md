# SDK 迭代记录：工具前置播报音频来源可配置

## 背景

`sdk-v80` 已经把工具前置播报改为由模型首输出类型自动判定：首输出是工具调用时播报 `ToolSpec.progress_message`，首输出是文本或音频时不插入等待提示。

真实听感验证后发现，预生成缓存音频虽然首包延迟低，但可能和实时生成的最终回复存在音色、情感和停顿差异。业务侧需要能按产品目标选择“更快的缓存播报”或“更一致的实时播报”。

## 变更

1. 新增服务端配置 `TOOL_PROGRESS_AUDIO_MODE`：
   - `cached`：启动阶段预生成或复用本地工具前置播报缓存。
   - `realtime`：工具调用前实时创建 TTS 流，边生成边下发。
2. SDK 默认值为 `cached`，保持已有低延迟缓存行为兼容。
3. 盲人业务本地配置模板默认设置为 `TOOL_PROGRESS_AUDIO_MODE="realtime"`，优先验证提示音和实时回复的一致性。
4. `cached` 模式读取本地 PCM 后，不再直接绕过播放合成框架，而是通过 `_emit_synthesis_chunk(...)` 和 `_finalize_synthesis_context(...)` 写入同一条下行播放路径。
5. `realtime` 模式会跳过启动阶段缓存预加载，工具调用前复用原有 `_synthesize_text_into_context(...)` 流式 TTS 路径。

## 配置示例

```bash
# 更关注首包延迟和 TTS 调用成本
TOOL_PROGRESS_AUDIO_MODE="cached"

# 更关注工具提示音和实时回复听感一致
TOOL_PROGRESS_AUDIO_MODE="realtime"
```

## 观察日志

`cached` 模式应看到：

```text
工具前置播报音频缓存预加载完成
工具前置播报命中静态音频缓存
```

`realtime` 模式应看到：

```text
工具前置播报音频缓存已跳过 mode=realtime
工具前置播报使用实时流式 TTS
```

两种模式下，眼镜端都只接收 `actuator.audio.play` 和 `/stream.wav` 下行播放流。

## 验证

- `uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_settings.py openaiglass-sdk/tests/unit/test_voice_runtime.py openaiglass-sdk/tests/unit/test_agent_core.py -q`
- `uv run openaiglass.glass.start --runtime playback --config openaiglass-for-blind/host/glass-playback/config/look_look.json`
  - 配置摘要确认 `tool_progress_audio_mode=realtime`。
  - 服务端启动日志确认 `工具前置播报音频缓存已跳过 mode=realtime`。
  - 回放结果 `assertions_ok=true`，眼镜端收到并播放 Omni Realtime 下行音频。
- 使用“我叫文刀，文字的文，刀锋的刀”样例做记忆工具回归：
  - 发现旧提示词仍要求模型“调用任何工具前先简单回复用户”，会诱导 Omni 先输出文本并跳过 `manage_memory`。
  - 已改为“工具调用前的等待提示由系统自动播报”，并明确姓名等基本信息必须调用 `manage_memory`，不能只用文字声称已经记住。
  - 回放验证恢复 `manage_memory` 工具调用，并在 `TOOL_PROGRESS_AUDIO_MODE=realtime` 下触发 `工具前置播报使用实时流式 TTS`。
- 使用“帮我设置一个三分钟的计时器”样例做临时回放验证：
  - Omni Realtime 触发 `start_timer` 工具调用，工具结果成功回填。
  - 本轮模型首输出先是文本和音频，随后才调用工具；按 `sdk-v80` 的自动判定规则，SDK 不插入工具前置播报，因此本次真实链路不会出现 `工具前置播报使用实时流式 TTS` 日志。
  - `TOOL_PROGRESS_AUDIO_MODE=realtime` 下“首输出即工具调用”的实时 TTS 分支由单元测试 `test_progress_reply_uses_realtime_tts_when_configured` 覆盖。
