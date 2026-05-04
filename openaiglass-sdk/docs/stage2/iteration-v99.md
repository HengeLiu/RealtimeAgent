# sdk-v99 语音运行时代码物理拆分

更新时间：2026-05-04

## 背景

`voice_runtime.py` 已经超过 7000 行，里面同时包含共享数据结构、Omni Realtime 客户端、ASR/TTS 客户端、播放队列、通知、Task 事件和设备会话编排。继续在一个文件里叠加逻辑会让 Omni Server / Text Server 的模态隔离难以落地，也会增加真机问题排查成本。

本轮在不改变设备协议和运行行为的前提下，先把纯客户端和共享模型迁出 `VoiceRuntime`。

## 变更

1. 新增 `runtime/voice_constants.py`。
   - 收敛语音采样率、播放队列、Omni semantic VAD 兜底等共享常量。
2. 新增 `runtime/voice_models.py`。
   - 收敛 `ModelChunk` 等模型流式分片数据结构。
3. 新增 `runtime/model_payloads.py`。
   - 收敛模型返回文本提取、音频 data URL 和对象/字典字段读取工具。
4. 新增 `runtime/omni/realtime_client.py`。
   - 迁入 `DashscopeOmniRealtimeReplyClient`、`OmniRealtimeStreamingSession`、`OmniRealtimeReplyResult` 和 Omni server event 摘要逻辑。
5. 新增 `runtime/text/speech_clients.py`。
   - 迁入 `VoiceModelClient`、`DashscopeVoiceModelClient`、`SpeechRecognitionClient`、`DashscopeSpeechRecognitionClient`、`StreamingTtsSession`、`DashscopeCosyVoiceTtsSession` 和实时 ASR 会话。
6. `runtime.voice_runtime` 保留兼容导入。
   - 业务或测试中从 `runtime.voice_runtime` 导入上述类仍然可用。
   - 本轮只做物理拆分，不改变热路径行为。
7. package-check 增加新模块导入覆盖。

## 效果

`voice_runtime.py` 从 7356 行下降到 4660 行。后续可以继续按播放、通知、Task 事件、会话状态机等维度拆分。

## 对业务开发者的影响

业务代码不需要修改。公开配置仍然是：

```yaml
voice:
  server_mode: omni_server
```

如果业务侧曾经从 `runtime.voice_runtime` 导入测试替身，本轮仍兼容；但新代码建议按真实归属从 `runtime.omni.realtime_client` 或 `runtime.text.speech_clients` 导入。

## 验证

```bash
uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_voice_server_boundaries.py -q

uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_voice_server_boundaries.py \
  openaiglass-sdk/tests/unit/test_settings.py \
  openaiglass-sdk/tests/unit/test_server_cli.py \
  openaiglass-sdk/tests/unit/test_package_check.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q

uv run --python 3.11 --with pytest --with setuptools --with wheel \
  openaiglass.sdk.package-check --repo-root .
```

结果：通过；package-check 返回 `ok: true`。
