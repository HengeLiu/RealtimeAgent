# SDK v48 迭代记录

## 背景

功能开发团队提出 SDK 应支持 Agent 长期记忆：Agent 需要主动记住用户行为习惯、基本信息和稳定偏好，也要允许用户通过自然语言要求新增或删除记忆。该能力跨业务 Tool、Task 和 Skill，应放在 SDK agent-core 中统一实现。

## 调研结论

本轮参考了 Letta / MemGPT、Mem0、LangGraph / LangChain 和 Zep / Graphiti 的公开方案。共同点是：记忆不应只是长聊天记录拼接，而应有持久化存储、作用域隔离、检索、删除和 Agent 可主动维护的工具面。

当前 SDK 第一版选择轻量本地实现，不直接引入外部服务：

1. 用 JSON 文件保存可审计记忆。
2. 先提供稳定接口和 Tool 语义。
3. 后续可在同一接口下替换为向量库、图数据库或外部记忆服务。

## 变更

1. 新增 `agent_core.memory` 模块：
   - `AgentMemoryRecord`
   - `AgentMemoryRuntime`
   - `InMemoryAgentMemoryStore`
   - `JsonFileAgentMemoryStore`
2. 新增模型可见工具 `manage_memory`，支持 `add/search/list/delete`。
3. `ToolRegistry` 支持注入 `AgentMemoryRuntime`，并在启用时自动暴露 `manage_memory`。
4. `AgentTurnRuntimeFactory` 每轮按当前 `device_id` 检索相关记忆，并注入系统提示词。
5. `model_request` 新增 `memory_prompt_fragment`，方便联调和回归产物核对。
6. `ServerSettings` 新增：
   - `AGENT_MEMORY_ENABLED`
   - `AGENT_MEMORY_STORE_PATH`
   - `AGENT_MEMORY_MAX_PROMPT_ITEMS`
7. `build_agent_facade_from_sdk(...)` 和默认服务端门面会按配置创建记忆运行时。
8. 更新 `SDK安装与能力开发指南.md` 到 `sdk-v48`。
9. 新增设计文档 `structure-design/Agent长期记忆设计.md`。

## 业务开发边界

业务能力不要自建长期记忆表、记忆 Tool 或提示词拼接逻辑。稳定偏好、基本信息和行为习惯交给 SDK 的 `manage_memory`；当前任务阶段、临时观测和短时状态继续放在 Task 上下文或当前会话。

## 风险和边界

1. 当前检索是轻量关键词匹配，不是语义向量检索。
2. 当前作用域按 `device_id` 隔离，尚未打通用户级和账号级记忆。
3. 当前支持新增、查询、列出和删除，尚未支持精确 update。
4. Agent 主动写入仍依赖模型按提示调用 `manage_memory`，后续可增加独立记忆抽取器和审计策略。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`

本轮未执行设备级回放。记忆能力第一版主要影响 agent-core 工具面和模型请求装配；后续与真实语音联调时应观察服务端 `model_request.memory_prompt_fragment`、`manage_memory` Tool trace 和 `runs/memory/agent_memories.json`。
