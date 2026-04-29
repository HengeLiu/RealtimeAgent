# Agent 长期记忆设计

## 背景

盲人业务能力团队希望 Agent 能主动记住用户的稳定偏好、基本信息和行为习惯，并允许用户通过自然语言要求新增或删除记忆。这类能力属于 SDK 系统层能力：它需要跨 Tool、Task、Skill 和多轮语音会话生效，不应由单个业务能力各自实现。

## 外部方案调研结论

本轮重点看了四类公开方案：

1. Letta / MemGPT：把记忆分成始终在上下文中的核心记忆和按需检索的外部归档记忆，Agent 通过工具主动维护记忆。
2. Mem0：把记忆抽象成 `add/search/update/delete` 这类操作，并用 user/session/run 标识做作用域隔离。
3. LangGraph / LangChain：长期记忆使用 Store 保存 JSON 文档，可按 semantic、episodic、procedural 三类理解不同记忆职责。
4. Zep / Graphiti：使用时间知识图谱组织会话、实体和关系，适合后续复杂用户画像和跨会话事实演化。

对当前 SDK 来说，第一轮不直接引入外部服务或图数据库。原因是：

1. 当前最紧急的是给功能开发团队一个稳定、可审计、可回放的 SDK 扩展面。
2. 业务链路仍在高频变化，外部图记忆或向量库过早引入会增加部署和排障成本。
3. 记忆接口先稳定后，底层存储可以从 JSON 文件替换成 Mem0、Zep、Graphiti 或向量库。

## 设计目标

1. 用户可通过自然语言让 Agent 记住、查询或删除记忆。
2. Agent 可主动保存稳定偏好、基本信息和行为习惯。
3. 每轮 Agent 请求自动注入相关记忆，但不把所有历史都塞进上下文。
4. 业务 Tool、Task、Skill 不直接操作底层记忆文件。
5. 记忆写入、删除和注入内容在调试产物中可观察。

## 当前实现

`sdk-v48` 新增以下模块：

```text
openaiglass-sdk/server-python/agent_core/memory/
  models.py      # AgentMemoryRecord
  store.py       # AgentMemoryStore / InMemoryAgentMemoryStore / JsonFileAgentMemoryStore
  runtime.py     # AgentMemoryRuntime
```

模型可见工具：

```text
manage_memory(action, text, query, memory_id, category, source, limit)
```

当前支持动作：

| action | 作用 |
| --- | --- |
| `add` | 新增一条短期可读的长期记忆。 |
| `search` | 按自然语言关键词查询记忆。 |
| `list` | 列出当前设备作用域下的记忆。 |
| `delete` | 按 `memory_id` 软删除记忆。 |

记忆作用域第一版按 `device_id` 隔离。这样能避免在账号体系和用户身份仍未完全产品化时，把不同设备或不同测试账号的记忆混在一起。

## Agent 请求装配

每轮 `AgentTurnRuntimeFactory.build(...)` 会：

1. 读取当前 `device_id` 对应的长期记忆。
2. 按当前用户输入做轻量关键词检索。
3. 若没有命中，则回退注入最近记忆。
4. 把最多 `AGENT_MEMORY_MAX_PROMPT_ITEMS` 条记忆注入系统提示词。
5. 将 `memory_prompt_fragment` 写入 `model_request`，方便回归排障。

系统提示词会要求模型：

1. 用户明确要求记住、忘记、删除或查看信息时，必须调用 `manage_memory`。
2. 发现稳定偏好、基本信息或行为习惯时，可以主动新增短记忆。
3. 不记录一次性任务、敏感密钥或未经确认的隐私信息。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AGENT_MEMORY_ENABLED` | `true` | 是否启用长期记忆。 |
| `AGENT_MEMORY_STORE_PATH` | `runs/memory/agent_memories.json` | 记忆文件路径。 |
| `AGENT_MEMORY_MAX_PROMPT_ITEMS` | `6` | 每轮最多注入记忆条数。 |

## 边界

适合写入长期记忆：

1. 用户偏好，例如“导航提示尽量简短”。
2. 用户基本信息，例如“用户习惯使用中文语音交互”。
3. 稳定习惯，例如“用户常在早上出门前检查眼镜电量”。

不适合写入长期记忆：

1. 当前任务阶段、当前路口状态、临时找物线索。
2. API Key、设备 token、WiFi 密码、真实用户媒体数据。
3. 一次性情绪、未经确认的推断和模型幻觉内容。

## 后续迭代

1. 增加用户级和账号级作用域，和设备组账号模型打通。
2. 增加 `update` 动作，支持精确修改已有记忆。
3. 增加语义向量检索，替换当前轻量关键词检索。
4. 增加记忆审计日志和回放断言，确认某轮是否写入、删除或注入记忆。
5. 评估接入 Mem0、Zep / Graphiti 或 LangGraph Store 作为可选后端。
