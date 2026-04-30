# SDK 迭代记录：长期记忆分类描述统一

对应对外 SDK 版本：`sdk-v67`。

## 背景

上一版长期记忆已经收敛为 MemoryAgent 内部动作计划，但分类描述容易让开发者误解为存储形态或缓存层级。本轮把长期记忆的对外描述统一为“基本信息”和“个性化信息”，让业务开发者更容易按内容语义判断应该保存什么。

## 本轮变更

1. `MemoryType` 只保留 `basic` 和 `personalized`：
   - `basic`：基本信息，例如姓名、年龄、性别、称呼等短小稳定信息。
   - `personalized`：个性化信息，例如住址、电话、爱好、习惯、任务设置等较长或可能变化的信息。
2. `LlmMemoryManagementAgent` 提示词和动作计划字段说明统一使用 `memory_type(basic/personalized)`。
3. `AgentMemoryRuntime.build_prompt_fragment(...)` 改为完整注入基本信息，只注入个性化信息主题。
4. `memory_search` 的输入说明改为按“记忆主题”查询，不再限定某一类记忆。
5. 本地 JSON 文件缺少 `memory_type` 时默认按 `personalized` 加载；不保留旧分类值兼容分支。
6. 更新长期记忆设计文档、能力开发指南、SDK 版本记录和相关单元测试。

## 业务开发边界

业务能力仍不应自建记忆表、记忆 Tool 或额外提示词拼接逻辑。主 Agent 只需要在用户表达记住、更新、忘记、删除等意图时调用 `manage_memory(query, memory_context)`；是否保存为基本信息或个性化信息，由 SDK MemoryAgent 根据自然语言、聊天上下文和已有记忆判断。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests -q`

本轮未执行设备级回放。改动集中在 agent-core 记忆分类语义、提示注入和工具说明，后续真实链路验证时应重点观察 `model_request.memory_prompt_fragment`、`manage_memory` Tool trace 和 `runs/memory/agent_memories.json`。
