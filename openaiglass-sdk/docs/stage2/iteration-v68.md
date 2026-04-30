# SDK 迭代记录：主 Agent 主动记忆提示补强

对应对外 SDK 版本：`sdk-v68`。

## 背景

`sdk-v67` 已经把长期记忆统一描述为基本信息和个性化信息，但主 Agent 提示词仍偏向“用户明确要求记住”才调用 `manage_memory`。这会导致用户自然说出“我叫小明”这类基本信息时，主 Agent 只回答用户，而不触发记忆保存。

## 本轮变更

1. 主 Agent 系统提示词新增主动记忆规则：用户自然说出值得长期保存的信息时，即使没有说“记住”，也应调用 `manage_memory`。
2. 明确应主动保存的基本信息：
   - 姓名、年龄、性别、称呼、语言偏好、沟通偏好。
3. 明确应主动保存的个性化信息：
   - 住址、常去地点、联系人称呼、导航偏好、出行习惯、饮食偏好、无障碍偏好、提醒或任务设置。
4. 明确不应保存的边界：
   - 一次性任务、当前路况、临时找物线索、敏感密钥、设备 token、WiFi 密码、真实用户媒体数据或未经确认的推断。
5. 补充单元测试，确保主 Agent 提示词包含主动记忆要求和保存边界。
6. 更新长期记忆设计文档、能力开发指南和 SDK 版本记录。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests -q`

本轮未执行设备级回放。后续真实链路验证时，应重点观察用户说出姓名、称呼、导航偏好后，模型是否调用 `manage_memory`，以及 `runs/memory/agent_memories.json` 是否出现对应记录。
