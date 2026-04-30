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

1. 用户可通过自然语言让 Agent 记住、更新或删除记忆。
2. Agent 可主动保存稳定偏好、基本信息和行为习惯。
3. 每轮 Agent 请求自动注入基本信息正文和个性化信息标题，但不把所有个性化信息详情都塞进上下文。
4. 业务 Tool、Task、Skill 不直接操作底层记忆文件。
5. 记忆写入、删除和注入内容在调试产物中可观察。

## 当前实现

`sdk-v67` 起，记忆模型分为两类：

| 类型 | 内容 | 注入方式 | 示例 |
| --- | --- | --- | --- |
| 基本信息 | 短小、稳定、不太变化的信息 | 每轮完整注入 system prompt | 姓名、年龄、性别 |
| 个性化信息 | 可能变化或内容较长的信息 | 每轮只注入标题，详情按需查询 | 住址、电话、爱好、习惯、任务设置 |

相关模块：

```text
openaiglass-sdk/server-python/agent_core/memory/
  models.py      # AgentMemoryRecord
  store.py       # AgentMemoryStore / InMemoryAgentMemoryStore / JsonFileAgentMemoryStore
  runtime.py     # AgentMemoryRuntime
```

模型可见工具：

```text
memory_search(title, titles)
manage_memory(query, memory_context)
```

工具分工：

| 工具 | 作用 |
| --- | --- |
| `memory_search` | 按标题读取记忆详细内容，不负责新增、更新或删除；未命中时也会返回文本反馈。 |
| `manage_memory` | 接收用户原始记忆请求和相关聊天上下文；内部交给 MemoryAgent 判断并执行一组新增、更新或删除动作。 |

记忆作用域第一版按 `user_id` 隔离；当前 `user_id=device_id`。这样能避免在账号体系和用户身份仍未完全产品化时，把不同设备或不同测试账号的记忆混在一起。服务端默认使用本地 JSON 文件持久化，默认路径为 `runs/memory/agent_memories.json`，重启程序不会丢失记忆；未来用户规模变大后再替换为数据库或外部记忆服务。

## Agent 请求装配

每轮 `AgentTurnRuntimeFactory.build(...)` 会：

1. 读取当前 `device_id` 对应的基本信息和个性化信息。
2. 把最多 `AGENT_MEMORY_MAX_PROMPT_ITEMS` 条基本信息完整注入 system prompt。
3. 把最多 `AGENT_MEMORY_MAX_PROMPT_ITEMS` 条个性化信息标题注入 system prompt。
4. 将 `memory_prompt_fragment` 写入 `model_request`，方便回归排障。

系统提示词会要求模型：

1. 回答需要某项个性化信息详情时，必须先调用 `memory_search`。
2. 用户明确要求记住、更新、忘记或删除信息时，必须调用 `manage_memory`。
3. 用户自然说出值得长期保存的信息时，即使没有说“记住”，也应调用 `manage_memory`。
4. 主动保存的基本信息包括姓名、年龄、性别、称呼、语言偏好和沟通偏好。
5. 主动保存的个性化信息包括住址、常去地点、联系人称呼、导航偏好、出行习惯、饮食偏好、无障碍偏好、提醒或任务设置。
6. `manage_memory` 不用于查询详情，搜索详情只走 `memory_search`。
7. 不记录一次性任务、当前路况、临时找物线索、敏感密钥、设备 token、WiFi 密码、真实用户媒体数据或未经确认的推断。

## 记忆管理子 Agent

`manage_memory` 不是直接 CRUD 工具。它只接收主 Agent 传入的自然语言请求和相关上下文：

```text
MemoryOperationRequest(
  query="用户原始请求",
  memory_context="历史聊天中与记忆相关的关键信息"
)
```

真实服务端默认使用 `LlmMemoryManagementAgent`。本轮取消 `HeuristicMemoryManagementAgent`：如果大模型不可用，长期记忆维护明确失败，不做启发式降级。原因是本产品主链路本身依赖大模型；启发式规则会让记忆维护在最需要准确理解用户意图的地方产生不可控误判。

MemoryAgent 会输出一组内部动作和给主 Agent 的简短反馈：

```text
MemoryOperationPlan(
  actions=[
    MemoryOperationAction(operation="delete", title="旧标题", memory_id="内部编号"),
    MemoryOperationAction(operation="add", memory_type="personalized", title="新标题", content="新内容")
  ],
  feedback="已更新相关记忆"
)
```

`memory_id` 只在 MemoryAgent 和 `AgentMemoryRuntime` 内部使用，用于定位已有记忆。主 Agent 调用 `manage_memory` 不需要、也不应该传入或看到 `memory_id`。

## 新增、更新和删除方案

长期记忆维护采用“主 Agent 提意图、记忆管理子 Agent 出计划、SDK runtime 执行计划”的三段式流程。这样可以让自然语言理解保持灵活，同时把真正的写入、覆盖、软删除和作用域隔离控制在 SDK 里。

### 1. 统一请求和执行入口

主 Agent 不直接读写 JSON 文件。用户说“记住我喜欢简短提示”“更新我的住址”“忘掉刚才那条记忆”时，主 Agent 只能调用模型可见工具 `manage_memory(...)`。工具只接受：

1. `query`：用户关于记忆管理的原始自然语言。
2. `memory_context`：主 Agent 从历史聊天中摘取的、与记忆维护有关的关键信息；没有时留空。
3. `metadata`：SDK 自动补充 `session_id`、`turn_id` 等审计信息。

`AgentMemoryRuntime.manage_memory(...)` 每次执行前会读取当前 user/device 作用域下最多 100 条有效记忆，并交给 `LlmMemoryManagementAgent.plan(...)` 生成动作计划。主 Agent 不传 `operation/title/content/memory_id/category/reason`，这些都由 MemoryAgent 根据自然语言和已有记忆自行判断。

### 2. 新增记忆

新增路径用于用户明确要求记录信息，或 Agent 判断某个稳定偏好、基本资料、行为习惯值得长期保存。

执行流程：

1. MemoryAgent 根据 `query`、`memory_context` 和现有记忆生成 `add` 动作，决定 `memory_type`、`title` 和 `content`。
2. `AgentMemoryRuntime.add_memory(...)` 清洗 `scope_id`、`title` 和 `content`，标题最长保留 60 个字符，正文最长保留 4000 个字符。
3. SDK 校验作用域、标题和正文不能为空；`confidence` 会被限制在 `0.0` 到 `1.0`。
4. 创建新的 `AgentMemoryRecord`，记录内部 `memory_id`、`scope_type/scope_id`、信息类型、标题、正文、来源、创建时间和更新时间。
5. 存储层优先调用 `upsert_by_title(...)`：同一作用域、同一信息类型、同一标题视为同一个记忆槽位。如果已有同标题记忆，则复用原 `memory_id` 和创建时间，避免重复记忆堆积。
6. `JsonFileAgentMemoryStore` 会在写入后把完整记忆列表刷新到 JSON 文件；写入采用临时文件加原子替换，降低异常退出造成半文件的概率。

新增策略的含义是：标题相同的信息默认是覆盖而不是追加。例如用户多次说“记住我的导航偏好”，SDK 会尽量维护同一条“导航偏好”记忆，而不是产生多条相互冲突的偏好。

### 3. 更新记忆

更新路径用于用户明确修改已有事实或偏好，例如“把我的名字更新为小李”“我的住址改成……”。更新不是简单新增一条新记录，而是尽量找到旧记录并复用其 `memory_id`。

执行流程：

1. MemoryAgent 输出 `update` 动作，并尽量给出内部 `memory_id` 或标题。
2. `AgentMemoryRuntime._find_update_target(...)` 按以下顺序寻找目标：
   - 先按 `memory_id` 精确匹配。
   - 再按动作标题匹配当前作用域下的有效记忆。
3. 如果找到目标，SDK 创建新的 `AgentMemoryRecord` 对象，但复用旧 `memory_id`、`scope_type/scope_id` 和 `created_at_ms`。
4. 新动作中的 `title`、`content`、`memory_type` 和请求元数据会覆盖或合并到旧记录上。
5. 存储层调用 `upsert(...)` 写回同一个 `memory_id`，更新时间刷新。
6. 如果找不到目标，当前实现会退化为一次新增。这个选择是为了不丢失用户明确要求保存的新信息；但如果用户表达的是“修改某条旧记忆”而目标不清楚，后续应考虑让主 Agent 追问确认。

更新策略的核心是保证引用稳定：只要能定位到旧记忆，就不换 `memory_id`。这样后续模型、调试产物或审计日志引用这条记忆时不会因为更新而失效。

### 4. 删除记忆

删除路径用于用户要求“忘掉”“删除”“别记住”某条信息。当前 SDK 使用软删除，不从 JSON 中物理移除记录，而是写入 `deleted_at_ms` 并刷新 `updated_at_ms`。软删除便于后续审计和问题回溯。

执行流程：

1. MemoryAgent 输出 `delete` 动作，尽量给出内部 `memory_id` 或标题。
2. 对“忘掉刚才那条记忆”“删除上一条信息”这类指代，由 MemoryAgent 根据传入的已有记忆列表自行判断目标，而不是由 SDK 启发式猜测。
3. `AgentMemoryRuntime.manage_memory(...)` 按以下顺序执行删除：
   - 按 `memory_id` 精确软删除。
   - 按标题但不限制信息类型软删除。
4. 如果所有路径都找不到目标，动作摘要会标记 `success=false`，反馈文本由 MemoryAgent 决定；主 Agent 应按反馈向用户说明未找到或需要更明确的信息。

删除策略必须保守：不能因为一句模糊表达删除多条记忆。当前一次 `manage_memory(delete)` 最多删除一条记录；批量删除、按类别删除和按时间范围删除应在后续引入确认机制后再开放。

### 5. 存储、排序和检索语义

当前第一版存储使用 `JsonFileAgentMemoryStore`，接口层抽象为 `AgentMemoryStore`，后续可替换为 SQLite、向量库、图数据库或外部记忆服务。

存储语义：

1. 有效记忆通过 `active` 判断，`deleted_at_ms is None` 表示有效。
2. 列表读取只返回当前 `scope_type/scope_id` 下的有效记忆；当前 `scope_id` 使用 `device_id` 作为 `user_id`。
3. 记忆列表按 `updated_at_ms` 倒序排列；如果多条记忆在同一毫秒写入，则按进程内写入顺序倒序排列，保证“刚才/上一条”指代稳定。
4. 搜索当前是轻量关键词检索：先做整句包含匹配，再做空格分词匹配；中文无空格时会用字符重叠做兜底打分。
5. `memory_search(title, titles)` 按标题读取记忆详情，不暴露内部 `memory_id`；未命中时返回“没有找到匹配的记忆”。

### 6. 当前风险和后续改进

当前方案已经能支撑本地可审计、可回放的长期记忆维护，但还不是完整生产级记忆系统：

1. 记忆维护完全依赖 MemoryAgent 的模型判断，模型不可用时不会降级执行。
2. 搜索不是语义向量检索，对同义表达、错别字和复杂中文标题召回有限。
3. 记忆作用域当前默认按 `device_id` 隔离，尚未升级到账号级、用户级或家庭成员级。
4. 删除目前是软删除，暂不做物理清理；批量删除必须由 MemoryAgent 输出多条动作并配合确认策略。
5. Agent 主动写入仍依赖主 Agent 按提示调用 `manage_memory`，后续可以增加独立记忆抽取器、用户确认策略和审计日志。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AGENT_MEMORY_ENABLED` | `true` | 是否启用长期记忆。 |
| `AGENT_MEMORY_STORE_PATH` | `runs/memory/agent_memories.json` | 记忆文件路径。 |
| `AGENT_MEMORY_MAX_PROMPT_ITEMS` | `6` | 每轮最多注入基本信息条数和个性化信息标题条数。 |

## 边界

适合写入长期记忆：

1. 基本信息：姓名、年龄、性别等短小稳定信息。
2. 个性化信息：住址、电话、爱好、习惯、任务设置等长内容或可能变化的信息。

不适合写入长期记忆：

1. 当前任务阶段、当前路口状态、临时找物线索。
2. API Key、设备 token、WiFi 密码、真实用户媒体数据。
3. 一次性情绪、未经确认的推断和模型幻觉内容。

## 后续迭代

1. 增加用户级和账号级作用域，和设备组账号模型打通。
2. 增加语义向量检索，用于个性化信息标题召回和近似标题匹配。
3. 增加记忆审计日志和回放断言，确认某轮是否写入、删除或注入记忆。
4. 评估接入 Mem0、Zep / Graphiti 或 LangGraph Store 作为可选后端。
