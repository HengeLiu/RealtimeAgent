# SDK v42 迭代记录

## 背景

2026-04-29 的真实链路日志显示，视觉问答在 ASR 完成后仍会先发起一轮“是否调用照片工具”的模型请求，再发起多模态图片解读请求。即使关闭思考模式，这一跳也会给首 token 增加约 2 秒延迟。

## 变更

1. 语音结束自动照片不再暴露为模型工具。
2. `UtterancePhotoStore` 增加一次性消费语义，只返回当前会话中已就绪、尚未使用的自动照片。
3. `AgentFacade.handle_turn(...)` 在进入 agent-core 前消费自动照片，把图片落盘为当前 turn 的 `MediaAssetRef`，并挂接到当前用户消息。
4. `OpenAIAgentLoopRunner` 组装当前 user message 时，如果 turn 中有图片资产，会发送 `content=[text, image_url...]` 的多模态输入；持久化的 `model_request` 会把图片 base64 脱敏为占位符。
5. 删除模型可见内置工具 `get_latest_utterance_photo`。
6. 默认 `AGENT_MODEL_NAME` 调整为 `qwen3.5-omni-plus`。

## 业务影响

1. 视觉问答类 Skill 不再需要把照片工具写入 `allowed_tools`。
2. 业务 Tool/Task 仍可通过 `DeviceGroupContext.capture_photo(...)` 主动控制设备抓拍；这不是模型默认可见工具。
3. 如果自动照片在当前 turn 进入 agent-core 前尚未上传完成，本轮会按纯文本输入执行；照片完成后会作为未使用照片进入后续 turn。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`

