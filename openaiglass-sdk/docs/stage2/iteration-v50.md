# SDK v50 迭代记录

## 背景

`sdk-v48` 已经提供统一长期记忆池，但它每轮会直接把检索到的完整记忆注入 system prompt，查询和管理也都塞在 `manage_memory` 里。为了降低上下文污染，并让长内容记忆可以按需读取，本轮按冷热两层重新收敛记忆模型。

## 变更

1. `AgentMemoryRecord` 新增冷热模型：
   - `memory_type=hot|cold`
   - `title`
   - `content`
2. 热记忆用于姓名、年龄、性别等短小稳定信息，每轮完整注入 system prompt。
3. 冷记忆用于住址、电话、爱好、习惯、任务设置等长内容或可能变化的信息，每轮只注入标题。
4. 新增 `memory_search` 工具：
   - 入参为 `title` 或 `titles`。
   - 只按冷记忆标题读取详细内容。
   - 不负责新增、更新或删除。
5. `manage_memory` 改为只负责新增、更新和删除，不再承担搜索或列表功能。
6. 新增记忆管理子 Agent 抽象：
   - `MemoryManagementAgent`
   - `LlmMemoryManagementAgent`
   - `HeuristicMemoryManagementAgent`
7. 真实服务端默认使用 `LlmMemoryManagementAgent`，模型不可用时退回确定性 fallback。
8. `model_request.memory_prompt_fragment` 改为保存热记忆正文和冷记忆标题目录。
9. 更新 `SDK安装与能力开发指南.md` 到 `sdk-v50`。

## 边界

1. 当前冷记忆详情查询仍是标题精确匹配，不是语义召回。
2. 当前记忆仍按 `device_id` 隔离，用户级和账号级作用域留到后续迭代。
3. 记忆管理子 Agent 负责生成结构化计划，真正落盘仍由 SDK 运行时执行。
4. `VOICE_REPLY_MODE=omni_realtime` 会绕过 agent-core，因此不会使用本轮长期记忆工具。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m py_compile openaiglass-sdk/server-python/agent_core/memory/*.py openaiglass-sdk/server-python/agent_core/tools/builtins/manage_memory.py openaiglass-sdk/server-python/agent_core/tools/builtins/memory_search.py openaiglass-sdk/server-python/agent_core/tools/registry.py openaiglass-sdk/server-python/agent_core/runtime/runner.py openaiglass-sdk/server-python/openaiglasses/server.py`
