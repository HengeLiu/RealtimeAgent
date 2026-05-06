# sdk-v102 进度播报缓存拆分

更新时间：2026-05-04

## 背景

工具前置播报缓存原先由 `VoiceRuntime` 直接管理，包含 ToolRegistry 读取、TTS 预生成、WAV/metadata 读写、过期缓存清理和运行时 PCM 命中逻辑。这部分能力属于播放前的共享优化能力，不应该和 Omni/Text 模型管线、设备会话状态混在一个类里。

本轮把工具前置播报缓存迁入独立 manager，继续缩小 `VoiceRuntime` 的职责。

## 变更

1. 新增 `runtime/progress_audio_cache.py`。
   - 新增 `ProgressAudioCacheManager`，负责启动预热、缓存指纹、TTS 合成、WAV 读取、metadata 校验、过期文件清理和运行时 PCM 查询。
2. `VoiceRuntime` 保留迁移期兼容入口。
   - `_progress_audio_cache`、`_progress_audio_cache_lock`、`_progress_audio_cache_ready` 仍指向 manager 内部同一份状态。
   - `_get_cached_progress_pcm(...)`、`_progress_audio_provider(...)` 等旧私有方法保留为委托包装。
3. package-check 增加 `runtime.progress_audio_cache` 导入覆盖。

## 效果

`voice_runtime.py` 从 4241 行下降到 4030 行。工具前置播报缓存不再直接依附模型管线，后续可以分别验证 `cached`、`realtime`、Omni Realtime 和 TTS 主链路。

## 验证

已执行：

```bash
uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_task_event_runtime.py \
  openaiglass-sdk/tests/unit/test_settings.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q

uv run --python 3.11 --with pytest --with setuptools --with wheel \
  openaiglass.sdk.package-check --repo-root .
```

结果：相关单测 129 条通过，package-check 返回 `ok: true`。

## 对业务开发者的影响

业务代码不需要修改。`ToolSpec.progress_message`、`tools.progress_audio.enabled`、`tools.progress_audio.mode=cached|realtime` 的使用方式保持不变。
