# SDK 迭代记录：Omni 音频直出支持工具调用

对应对外 SDK 版本：`sdk-v75`。

## 背景

`sdk-v74` 已经让音频原生 Chat Completions 分支改为流式文本返回，但真实语音体验仍容易落到“模型文本增量 + CosyVoice TTS”。这会让支持音频输出的 Omni 模型没有充分发挥低延迟优势。

同时，Omni 音频直出和工具调用本身并不冲突：模型可以先输出自然语音反馈，再触发 function calling；SDK 执行工具并回填结果后，模型继续输出最终音频。已经播放给用户的前置语音不应默认取消。

## 本轮变更

1. 默认 `VOICE_REPLY_MODE=omni_realtime` 重新接回 Omni Realtime 音频直出 pipeline。
2. `sensor.audio.segment.started` 时预连接 Omni Realtime，并把上行音频分片同步追加到 Omni 会话。
3. Omni Realtime session 会携带当前模型可见 SDK Tool schema。
4. 新增 Realtime 工具桥，监听 `response.function_call_arguments.done`，执行 SDK `ToolGateway`，再以 `function_call_output` 回填给 Omni 并继续请求文本与音频输出。
5. 工具执行不默认取消已经播放的模型音频；工具成功或失败都作为后续上下文交给模型继续播报。
6. 新增 `ENABLE_PROGRESS_MESSAGE` 配置：
   - `true`：SDK 使用 `ToolSpec.progress_message` 和静态音频缓存播报工具前置提示。
   - `false`：SDK 不播预置提示，由模型在调用工具前自然输出等待反馈。
7. 更新功能开发指南、配置模板和相关单元测试。

## 验证

1. `python -m py_compile openaiglass-sdk/server-python/runtime/voice_runtime.py openaiglass-sdk/server-python/agent_core/runtime/runner.py openaiglass-sdk/server-python/infra/config/settings.py`
2. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
3. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py openaiglass-sdk/tests/unit/test_settings.py -v`

本轮未执行真实设备级回放。真机联调时应重点观察 `Omni Realtime 工具调用请求`、`Omni Realtime 工具结果已回填`、`Omni Realtime 返回首段音频` 和眼镜端 `播放流收到首段 PCM`。
