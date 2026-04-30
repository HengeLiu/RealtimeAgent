# SDK 迭代记录：工具前置播报静态音频缓存

对应对外 SDK 版本：`sdk-v70`。

## 背景

`sdk-v69` 已经支持工具执行前置播报，但前置播报仍需要实时请求 TTS。对于 `progress_message` 这类静态短文本，每次工具调用都重新合成会增加首播延迟，也会产生重复 TTS 调用费用。

## 本轮变更

1. `ToolRegistry` 新增 `list_progress_messages()`，用于汇总当前注册工具的静态前置播报文案。
2. `VoiceRuntime` 启动后异步预加载工具前置播报音频缓存，缓存文件位于 `VOICE_RUNS_ROOT/progress-audio-cache`。
3. 缓存键按播报文本、TTS 模型、音色、TTS 采样率和目标播放采样率生成，避免配置变更后误用旧音频。
4. 缓存文件存在且格式符合 16k 单声道 16bit PCM WAV 时直接加载到内存。
5. 缓存不存在时，启动阶段调用当前配置的 TTS 生成一次 WAV；生成失败不阻塞服务启动。
6. 工具调用时命中缓存会直接读取本地 PCM 写入播放流，不再请求 TTS；缓存未就绪、未命中或失败时自动回退实时 TTS。
7. 更新 `普通文本流式与TTS首包延迟优化设计.md`、`SDK安装与能力开发指南.md` 和 `sdk-version`。

## 业务开发边界

业务 Tool 仍然只需要声明一句简短、口语化的 `progress_message`。业务代码不需要也不应该管理音频文件、调用 TTS、写播放控制消息或访问播放仲裁器。

如果业务侧频繁调整 `progress_message`、TTS 模型、音色或采样率，SDK 会自动生成新的缓存文件；旧缓存位于运行产物目录，可按运维需要手动清理。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
3. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q`

本轮未执行设备级回放。后续真实链路验证时应重点观察 `工具前置播报音频缓存预加载完成`、`工具前置播报命中静态音频缓存`、前置播报播放流和最终回复是否按顺序进入播放仲裁。
