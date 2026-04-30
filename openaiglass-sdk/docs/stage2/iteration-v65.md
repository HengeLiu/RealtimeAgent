# SDK 迭代记录：Agent 长期记忆自然语言更新删除增强

对应对外 SDK 版本：`sdk-v65`。

## 背景

功能开发团队需要 Agent 能主动记住用户的稳定偏好、基本信息和行为习惯，也要允许用户通过自然语言主动新增、更新或删除记忆。SDK 已在 `sdk-v50` 提供长期记忆、`memory_search` 和 `manage_memory`，但无模型兜底路径对“忘掉刚才那条记忆”“删除我的导航偏好”等自然语言控制不够稳。

本轮继续沿用 SDK 自研轻量记忆运行时，不引入外部依赖。调研结论仍保持：Mem0、Letta / MemGPT、LangGraph 和 Zep / Graphiti 都说明长期记忆应有明确存储、检索、更新和删除语义；当前 SDK 先稳定接口和可回放行为，后续再评估向量库、图数据库或外部记忆服务。

## 本轮变更

1. `HeuristicMemoryManagementAgent` 增强中文删除指令解析，支持从“删除我的导航偏好”“忘掉刚才那条记忆”等表达中提取目标。
2. `AgentMemoryRuntime.manage_memory(...)` 将 `add` 和 `update` 分开执行；`update` 会优先复用 `memory_id`，避免更新后引用失效。
3. 删除流程增加多级兜底：先按 `memory_id`，再按主题和类型，再按主题不限定类型，最后按原始自然语言查询匹配。
4. 记忆存储增加写入顺序记录；当多条记忆在同一毫秒写入时，“最近一条”仍能稳定指向最后写入的记录。
5. 新增单元测试覆盖无模型环境下的自然语言删除、最近记忆删除和按 `memory_id` 更新。
6. 更新 `SDK安装与能力开发指南.md`、`Agent长期记忆设计.md` 和 `sdk-version`。

## 业务开发边界

业务能力仍不应自建记忆表、记忆 Tool 或额外提示词拼接逻辑。用户稳定偏好、基本信息和行为习惯交给 SDK 的 `manage_memory`；业务当前任务阶段、临时状态和回放观测仍放在 Task 上下文或本轮会话里。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`

本轮未执行设备级回放。改动集中在 agent-core 记忆运行时和模型工具语义，后续真机或回放验证时应重点观察 `manage_memory` Tool trace、`model_request.memory_prompt_fragment` 和 `runs/memory/agent_memories.json`。
