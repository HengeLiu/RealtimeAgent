# SDK 迭代记录：音频原生链路流式返回

对应对外 SDK 版本：`sdk-v74`。

## 背景

`OpenAIAgentLoopRunner._run_direct_audio_turn()` 是音频原生 Chat Completions 分支，用于把当前轮 WAV 音频和自动照片直接交给 Omni 主模型，并保留 Tool 调用能力。此前这条分支使用 `stream=False`，即使上层提供了 `reply_text_delta_callback`，也只能等最终文本完整返回后一次性回调，导致 TTS 首包延迟高于普通文本和图片解读链路。

## 本轮变更

1. 音频原生 Chat Completions 请求改为 `stream=True`。
2. 新增流式消费逻辑，持续提取 `choices[].delta.content` 并透传给 `reply_text_delta_callback`。
3. 新增工具调用分片累积逻辑，支持从 `choices[].delta.tool_calls` 组装完整工具调用。
4. 工具调用仍在流结束后通过 `ToolGateway` 执行，工具结果回填后下一轮模型请求继续使用流式模式。
5. 保持旧的手写工具循环上限和模型请求快照结构。
6. 更新 `普通文本流式与TTS首包延迟优化设计.md`、`SDK安装与能力开发指南.md` 和 `sdk-version`。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`

本轮未执行设备级回放。真机验证时应观察音频原生链路最终回复是否在模型完整结束前开始进入 TTS 播放。
