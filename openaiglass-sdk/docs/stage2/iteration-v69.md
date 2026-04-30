# SDK 迭代记录：工具调用前置播报

对应对外 SDK 版本：`sdk-v69`。

## 背景

当前 `agent_tts` 链路在工具调用场景下仍可能出现明显静默等待：模型需要先决定工具调用，SDK 再执行工具，工具完成后模型才生成最终回复。调研 OpenAI Realtime / Responses 工具调用事件后，本轮不把“模型在返回工具调用前先说等待语”作为稳定契约，而是在 SDK 工具执行入口提供框架级前置播报。

## 本轮变更

1. `ToolSpec` 新增 `progress_message`，用于声明工具执行前的短提示。
2. `AgentToolContext` 新增 `progress_callback` 和单轮去重记录。
3. `ToolGateway` 在工具真正执行前触发一次 `progress_message`，同一轮同一工具只播报一次。
4. 公开 SDK `BaseTool` 新增 `progress_message`，`SdkToolAdapter` 会透传到 agent-core。
5. 内置工具 `query_device_state`、`query_task_status`、`cancel_task`、`capture_photo` 和 `start_phone_video_link` 增加默认前置播报。
6. `manage_memory` 和 `memory_search` 增加默认前置播报，避免记忆管理子 Agent 请求期间静默等待。
7. 最终回复 TTS 仍在 Agent 请求前预热，但最终回复播放流延迟到首个最终回复文本到达时才注册，避免预热流占住播放仲裁器。
8. 中间播报改为先同步注册播放流，再异步执行 TTS 合成，确保后续最终回复排在前置播报之后。
9. 更新 `普通文本流式与TTS首包延迟优化设计.md`、`SDK安装与能力开发指南.md` 和 `sdk-version`。

## 业务开发边界

业务 Tool 只需要声明一句简短、口语化的 `progress_message`，不要自行调用播放器、TTS、WebSocket 控制消息或播放仲裁器。前置播报只覆盖“模型已经决定调用工具后，工具执行期间”的静默等待；模型首轮决策前的延迟仍由 SDK 通过 ASR 前移、模型/工具面收敛、TTS 预热和 Realtime 链路继续优化。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
3. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q`

本轮未执行设备级回放。改动集中在 agent-core ToolGateway、公开 Tool 适配和语音中间播报触发点；后续真实链路验证时应重点观察 `tool.call` 日志、前置播报播放流和最终回复是否按顺序进入播放仲裁。
