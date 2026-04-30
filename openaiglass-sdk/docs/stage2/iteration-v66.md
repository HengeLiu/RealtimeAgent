# SDK 迭代记录：Agent 长期记忆维护语义收敛

对应对外 SDK 版本：`sdk-v66`。

## 背景

上一版长期记忆虽然支持新增、更新和删除，但仍把过多结构化字段暴露给主 Agent，并保留了无模型启发式兜底。长期记忆维护本质上需要理解用户自然语言、聊天上下文和已有记忆之间的关系，应由专门的 MemoryAgent 决定具体动作，而不是让主 Agent 拼装 CRUD 参数。

## 本轮变更

1. 移除 `HeuristicMemoryManagementAgent`，模型不可用时记忆维护明确失败，不做规则降级。
2. `ManageMemoryInput` 收敛为 `query` 和 `memory_context` 两个字段。
3. `MemoryOperationRequest` 不再包含 `operation/topic/content/memory_id/category/source` 等主 Agent 不应关心的字段。
4. 新增 `MemoryOperationAction`，`MemoryOperationPlan` 改为动作列表，支持一次请求内串行执行多个动作，例如先删除再新增。
5. `memory_id` 只在 MemoryAgent 与 `AgentMemoryRuntime` 内部使用；`manage_memory` 和 `memory_search` 返回给主 Agent 的结果不再包含内部编号。
6. 记忆记录移除 `category` 字段；`reason` 不再作为计划字段。
7. `memory_search` 改为按主题读取记忆详情，未命中时返回文本反馈“没有找到匹配的记忆”。
8. `AgentMemoryRuntime` 默认使用本地 JSON 文件存储，真实服务端继续通过 `AGENT_MEMORY_STORE_PATH` 配置路径。
9. 更新 `Agent长期记忆设计.md`、`SDK安装与能力开发指南.md` 和 `sdk-version`。

## 业务开发边界

业务能力仍不应自建记忆表、记忆 Tool 或额外提示词拼接逻辑。主 Agent 只需要在用户表达记住、更新、忘记、删除等意图时调用 `manage_memory(query, memory_context)`；是否新增、更新、删除、拆成几步动作，全部由 SDK MemoryAgent 决定。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`

本轮未执行设备级回放。改动集中在 agent-core 记忆工具入参、内部动作计划和公开返回语义，后续真实链路验证时应重点观察 `manage_memory` Tool trace、`model_request.memory_prompt_fragment` 和 `runs/memory/agent_memories.json`。
