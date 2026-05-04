# sdk-v93 模型工具 reason 参数收敛

更新时间：2026-05-04

## 背景

真机联调中，`close_continuous_dialog` 工具调用结果里出现了由模型生成的 `reason` 字段，例如“用户表达希望助手安静，结束连续对话”。这个字段对 SDK 执行关闭连续对话没有实际必要，反而会让业务提示词误以为所有工具都必须要求模型解释调用原因。

本轮把模型可见工具契约收敛为“默认不需要 reason”。SDK 内部运行时如果需要记录关闭原因、播放原因或协议原因，继续使用系统默认值，不再要求模型或业务提示词提供。

## 变更

1. `close_continuous_dialog` 工具输入只保留 `mode`，工具结果只返回 `scheduled` 和 `mode`。
2. `capture_photo` 内置工具不再要求模型传入 `reason`，抓拍网关使用 SDK 默认系统原因。
3. `start_phone_video_link` 内置工具不再要求模型传入 `reason`。
4. `DeviceGroupContext.stop_phone_task(...)` 的 `reason` 参数改为可选，业务 Task 默认不需要提供。
5. 更新 SDK 安装与能力开发指南，说明模型工具默认不需要 `reason`，运行时日志里的原因是 SDK 系统字段。

## 对业务开发者的影响

1. 提示词中不要再要求模型为工具调用生成 `reason`。
2. 业务 Tool/Task 通过 `DeviceGroupContext` 控制设备时，也可以省略 `reason`，除非业务确实需要在自己的日志里记录细分原因。
3. 看到 `voice.dialog.close` 控制消息中带有 `reason=model_requested` 是正常现象，这表示 SDK 运行时默认关闭原因，不是模型生成内容。

## 验证

本轮建议验证：

```bash
python -m py_compile \
  openaiglass-sdk/server-python/agent_core/tools/builtins/close_continuous_dialog.py \
  openaiglass-sdk/server-python/agent_core/tools/builtins/capture_photo.py \
  openaiglass-sdk/server-python/agent_core/tools/builtins/start_phone_video_link.py \
  openaiglass-sdk/server-python/openaiglasses/runtime/device_group.py

uv run python -m unittest \
  openaiglass-sdk.tests.unit.test_agent_core \
  openaiglass-sdk.tests.unit.test_voice_runtime \
  openaiglass-sdk.tests.integration.test_agent_phase_e_flow \
  -v
```
