# iteration-v23：SDK v24 Agent 模型兼容性与可观测性

## 本轮目标

根据 2026-04-28 的联调反馈，修复 `AGENT_MODEL_NAME=qwen-turbo` 时设备侧只看到超时、服务端没有明确异常日志的问题。

本轮对应对外 SDK 版本：`sdk-v24`。

## 主要改动

1. `VoiceRuntime` 在音频段进入 ASR 前打印 INFO 日志，包含输入流、音频段、时长、字节数、ASR 模型和 Agent 模型。
2. `VoiceRuntime` 在 ASR 完成后打印 INFO 日志，明确即将进入 agent-core。
3. `OpenAIAgentLoopRunner` 在调用模型前打印 INFO 日志，包含模型名、运行模式、消息数、工具数和超时时间。
4. agent-core 结构化失败和非结构化失败改为 ERROR 日志，不再隐藏在 DEBUG 中。
5. 对 `qwen-turbo`、`qwen-plus`、`qwen-max` 这类不适合当前 `stream=True + tools` 组合的模型直接返回 `INVALID_CONFIG`，避免设备侧等待超时。
6. 流式 Agent 调用增加 SDK 层超时保护，超过 `VOICE_MODEL_TIMEOUT_MS` 后返回结构化失败。

## 当前边界

1. 当前语音链路依赖流式文本增量进入 TTS，同时需要 SDK Tools 支持 Task、MCP 和硬件能力调用，因此默认使用流式 Agent + tools。
2. 非流式工具模式尚未实现；如果要使用只支持非流式工具调用的模型，需要后续增加独立运行模式。
3. 用户贴出的日志只到设备注册和实时会话降级，没有出现 `语音链路开始处理音频段`，说明那段日志本身还不能证明已经进入模型调用。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py -q
```
