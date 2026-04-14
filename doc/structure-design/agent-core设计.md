# agent-core 设计

## 1. 文档定位

本文档是当前项目 `agent-core` 的**最终设计定稿**。

本文档只保留已经收敛后的架构结论、模块边界、运行时对象、任务运行时设计和实施约束，不再展开方案调研、选型比较和解释性讨论。

方案调研、适配性分析和选型理由已迁移到：

- [agent-core方案调研与OpenAI Agents SDK选型说明.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/experimental/agent-core方案调研与OpenAI%20Agents%20SDK选型说明.md)

`backend-task-core` 的详细设计已独立到：

- [backend-task-core设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/backend-task-core设计.md)

---

## 2. 设计目标

本设计用于支撑以下目标：

1. 在当前已完成的语音主链路之上，引入完整 `agent-core`。
2. 用统一方式承接 Tool、MCP、后台任务调用。
3. 为后续拍照解读、导航和后台任务管理提供稳定运行时基座。
4. 允许更多开发者在统一框架下扩展模型能力，而不需要修改核心运行循环。

---

## 3. 最终设计结论

### 3.1 总体结论

当前项目采用如下策略：

1. `agent-core` 的通用运行循环基于 **OpenAI Agents SDK** 实现。
2. `server-api`、`voice-runtime`、设备控制链路、媒体链路继续自研。
3. `backend-task-core` 继续作为独立 `task runtime` 存在。
4. 短期内不引入复杂 Skill Runtime，Skill 先简化为“业务型高级 Tool”。

### 3.2 核心边界

#### OpenAI Agents SDK 承担

1. `agent loop`
2. tool calling
3. sessions
4. MCP 接入
5. tracing
6. handoff
7. human-in-the-loop

#### 项目继续自研

1. `server-api`
2. `voice-runtime`
3. 图片抓拍链路
4. 媒体资产存储
5. `backend-task-core`
6. 任务模板
7. 设备执行策略

---

## 4. 总体架构

服务器侧采用如下分层：

1. `server-api`
2. `voice-runtime`
3. `agent-core`
4. `backend-task-core`
5. `tool / skill / mcp / task` 能力层
6. `asset/context store`

### 4.1 `server-api`

职责：

1. 接收控制连接与媒体连接。
2. 维护 `/ws/control`、`/ws_audio`、`/stream.wav`、后续相机通道等接入点。
3. 路由设备输入到对应运行时。

不负责：

1. 不负责模型决策。
2. 不负责长期任务调度。

### 4.2 `voice-runtime`

职责：

1. 管理语音会话。
2. 聚合用户单轮音频。
3. 调用 ASR。
4. 把语音输入转换成 `AgentTurn`。
5. 把最终回复转换为音频并完成播放控制。

不负责：

1. 不负责业务能力选择。
2. 不负责长期任务管理。

### 4.3 `agent-core`

职责：

1. 维护开放式会话上下文。
2. 承担 `agent loop`。
3. 决定：
   - 直接回复
   - 追问澄清
   - 调用 Tool
   - 调用 Skill
   - 调用 MCP
   - 创建、查询、取消任务
4. 把能力调用结果写回上下文。
5. 统一处理错误和最终回复生成。
6. 按需要直接调用各种工具和远程能力。

不负责：

1. 不直接维护设备连接。
2. 不直接管理媒体流。
3. 不直接维护任务状态机。

### 4.4 `backend-task-core`

职责：

1. 创建任务实例。
2. 管理任务状态机。
3. 调度任务生命周期。
4. 输出任务事件。
5. 接受查询、取消、暂停、恢复请求。
6. 在后台执行过程中直接调用 Tool、Skill、MCP 等原子能力。

不负责：

1. 不负责开放式对话决策。
2. 不负责自然语言理解。

---

## 5. 当前最小运行时与完整 agent-core 的关系

当前已落地的 `VoiceSessionController` 保留，但其定位调整为：

**语音输入输出边界组件**

而不是：

**完整的 agent-core**

### 5.1 当前 `VoiceSessionController` 继续负责

1. 单设备语音会话。
2. 当前轮音频聚合。
3. ASR 接入。
4. 回复播放控制。

### 5.2 完整接入后的协作方式

推荐流程：

1. `voice-runtime` 完成当前轮 ASR。
2. 把 `user_text + asset_refs + derived_refs` 提交给 `agent-core`。
3. `agent-core` 返回：
   - 最终文本回复
   - 或能力调用结果
   - 或需要追问的问题
4. `voice-runtime` 负责音频播报和设备控制。

---

## 6. 关键概念定义

### 6.1 Tool

`Tool` 是单次函数调用即可完成的原子能力。

示例：

1. `query_device_state`
2. `capture_photo`
3. `create_timer`
4. `query_task_status`
5. `cancel_task`

### 6.2 Skill

短期定义：

**Skill = 业务型高级 Tool**

即：

1. 对模型表现为 tool。
2. 对项目代码而言，内部可以组合多个 Tool、MCP 或任务接口。

示例：

1. `photo_interpret`
2. `prepare_navigation`
3. `timer_manage`

### 6.3 MCP

`MCP` 是远程工具服务接入层。

特点：

1. 面向远程原子方法。
2. 负责参数转换、超时、错误包装。
3. 优先被 Tool 或 Skill 包装后使用。

示例：

1. `amap.poi_search`
2. `amap.geocode`
3. `amap.route_plan`

### 6.4 Task

`Task` 是未启动的后台任务模板代码，是与 `Tool / Skill / MCP` 同等级的一等扩展单元。

特点：

1. 表示一种可注册、可发现、可复用的后台能力模板。
2. 生命周期跨越单次模型调用，但在启动前并不是实例。
3. 允许在内部自由组装 Tool、Skill、MCP、设备网关和其他基础能力。
4. 由开发者按统一框架扩展，并由 `backend-task-core` 负责注册和托管。

示例：

1. `timer_task`
2. `navigation_task`
3. `phone_video_link_task`

### 6.5 TaskRuntime

`TaskRuntime` 表示某个 `Task` 被实际创建后的后台任务实例。

特点：

1. 对应唯一 `task_id`。
2. 有明确状态机和运行上下文。
3. 会持续产出事件、状态和结果。
4. 由 `backend-task-core` 创建、调度和托管。

---

## 7. OpenAI Agents SDK 下的能力映射

## 7.1 Tool 映射

当前项目里的 Tool 直接映射为 OpenAI Agents SDK function tools。

首批 Tool：

1. `query_device_state`
2. `capture_photo`
3. `create_timer`
4. `query_task_status`
5. `cancel_task`

约束：

1. 一个 Tool 对应一个明确函数。
2. 函数签名定义 schema。
3. docstring 用中文写清楚功能、参数、返回值和异常情况。

## 7.2 Skill 映射

短期内不建立独立 Skill Runtime。

采用两种实现形态：

1. `Tool 包装型 Skill`
2. `Agent-as-Tool 型 Skill`

默认优先采用：

1. `Tool 包装型 Skill`

## 7.3 MCP 映射

MCP 尽量直接接入 OpenAI Agents SDK 原生 MCP 支持。

AMap 建议暴露的原子方法：

1. `amap.poi_search`
2. `amap.geocode`
3. `amap.route_plan`

## 7.4 Task 映射

`Task` 与 `TaskRuntime` 都不映射为 OpenAI Agents SDK 原生对象。

原则：

1. SDK 负责任务相关决策和开放式调用链路。
2. 项目自己的 `backend-task-core` 负责 `Task` 注册和 `TaskRuntime` 运行。

---

## 8. 上下文与资产模型

完整 `agent-core` 保留并扩展以下模型：

1. `MessageContext`
2. `MediaAssetRef`
3. `DerivedArtifact`
4. `CapabilityTrace`
5. `TaskRef`

### 8.1 MessageContext

每条消息至少包含：

1. `message_id`
2. `session_id`
3. `role`
4. `kind`
5. `text`
6. `asset_refs`
7. `derived_refs`
8. `task_refs`
9. `meta`

### 8.2 MediaAssetRef

至少包含：

1. `asset_id`
2. `asset_type`
3. `storage_uri`
4. `mime_type`
5. `codec`
6. `duration_ms / width / height / fps`
7. `source_stream_id`

### 8.3 DerivedArtifact

用于保存：

1. 语音转写文本
2. 图片摘要
3. 地图查询结果
4. 路线摘要
5. 检测结果
6. 任务查询结果

### 8.4 TaskRef

用于表示会话与后台任务的关联关系。

---

## 9. 最小运行时对象

完整 `agent-core` 至少包含以下对象：

1. `AgentSession`
2. `AgentTurn`
3. `DialogState`
4. `CapabilityTrace`
5. `TaskRef`
6. `VoiceSessionController`

### 9.1 AgentSession

表示一条开放式会话。

### 9.2 AgentTurn

表示一次输入处理单元。

输入来源包括：

1. 语音转写文本
2. 文本输入
3. 图片抓拍结果
4. 任务完成事件
5. 任务状态变化事件

### 9.3 DialogState

用于表示：

1. 当前待确认信息
2. 当前待补齐参数
3. 当前追问状态

### 9.4 CapabilityTrace

记录一次 turn 中所有 Tool、Skill、MCP 调用轨迹。

---

## 10. task runtime 设计

这是当前方案里风险最高、优先级最高的部分。

本章只保留系统级摘要，`task-core` 的详细设计以 [backend-task-core设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/backend-task-core设计.md:1) 为准。

## 10.1 定位

`task runtime` 即与 `agent-core` 平级的 `backend-task-core`。

其系统定位是：

1. 统一承接所有长生命周期后台任务。
2. 作为 `Task` 模板的注册、创建、调度和托管中心。
3. 独立于 `agent-core` 运行，但与 `agent-core` 通过标准事件和网关协作。

## 10.2 系统边界

`agent-core` 负责：

1. 理解用户意图。
2. 决定是否需要创建、查询、取消后台任务。
3. 在对话执行链路中直接调用 Tool、Skill、MCP。
4. 在收到任务事件后决定是否追问、确认、播报或继续调用其他能力。

`backend-task-core` 负责：

1. 注册和发现 `Task` 模板。
2. 创建并托管 `TaskRuntime` 实例。
3. 管理任务状态机、调度、上下文和事件流。
4. 在后台执行过程中直接调用 Tool、Skill、MCP 与设备能力。

## 10.3 协作原则

双方协作遵循以下原则：

1. `Task` 是未启动的后台任务模板，`TaskRuntime` 是启动后的实例。
2. `agent-core` 与 `backend-task-core` 都允许直接调用各种工具能力。
3. `backend-task-core` 必须先产出结构化事件，再由统一通知策略决定是否直接通知设备。
4. 默认任务事件先回流 `agent-core`；高优先级或安全相关事件允许绕过 `agent-core` 直接通知，同时仍需回流标准事件用于上下文同步。

典型链路如下：

1. 用户输入进入 `agent-core`。
2. `agent-core` 通过 Tool 或其他受控入口创建后台任务。
3. `backend-task-core` 创建 `TaskRuntime` 并开始运行。
4. 任务运行过程中持续产出标准事件。
5. 事件根据优先级决定是仅回流 `agent-core`，还是先直达设备再回流 `agent-core`。

## 10.4 第一期范围

第一阶段在主设计层只要求：

1. 完成 `timer_task` 的最小闭环。
2. 打通任务创建、查询、取消、完成通知。
3. 固定 `Task / TaskRuntime / TaskEvent / TaskGateway` 的基本边界。

更细的对象模型、模块拆分、事件字段和扩展规范，统一放在 [backend-task-core设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/backend-task-core设计.md:1) 中维护。

---

## 11. Agent Loop 设计

一轮 `AgentTurn` 的处理流程如下：

1. `TurnBuilder`
2. `ContextAssembler`
3. `Planner`
4. `DecisionRouter`
5. `Executor`
6. `ContextUpdater`
7. 若未完成，则继续下一步

建议动作类型至少包括：

1. `final_answer`
2. `ask_user`
3. `call_tool`
4. `call_skill`
5. `call_mcp`
6. `create_task`
7. `query_task`
8. `cancel_task`
9. `fail`

---

## 12. 第 4-8 项的落地映射

## 12.1 第 4 项：AgentCore 调工具

落地项：

1. OpenAI Agents SDK 作为主循环
2. `AgentFacade`
3. 第一批 Tool

## 12.2 第 5 项：Skills 与 MCP

落地项：

1. MCP 接入
2. 简化 Skill

首批建议：

1. `photo_interpret`
2. `timer_manage`
3. `amap` MCP adapter

## 12.3 第 6 项：拍照 Skill + 图片解读

设计：

1. `capture_photo` 作为 Tool
2. `photo_interpret` 作为业务型高级 Tool
3. 图片抓拍和资产存储继续自研

## 12.4 第 7 项：AMap MCP 导航

设计：

1. AMap 作为 MCP
2. `prepare_navigation` 作为业务型高级 Tool
3. `navigation_task` 作为长期任务

## 12.5 第 8 项：后台任务管理 Skill

设计：

1. `create_timer`
2. `query_task_status`
3. `cancel_task`
4. `timer_task`

原则：

1. Agent 负责决策。
2. Task Runtime 负责执行。
3. `agent-core` 与 `backend-task-core` 都允许直接调用各种工具。

---

## 13. 扩展规范

## 13.1 新增 Tool

需要完成：

1. 实现 Python 函数
2. 中文 docstring
3. 在 `AgentFacade` 注册
4. 补测试

禁止：

1. 在主循环硬编码特殊逻辑

## 13.2 新增 Skill

短期内：

1. 以业务型高级 Tool 方式扩展

## 13.3 新增 MCP

需要完成：

1. MCP adapter 定义
2. 方法清单
3. 超时和错误包装
4. 注册

## 13.4 新增 Task

需要完成：

1. 实现 `Task` 模板代码
2. 定义输入、上下文和状态机
3. 在 `TaskRegistry` 注册
4. 说明内部可组装的 Tool、Skill、MCP 与设备能力
5. 通过 Tool、Task 或受控入口发起创建

---

## 14. 推荐目录结构

建议后续目录结构如下：

```text
server/src/agent_core/
  facade/
  session/
  context/
  runtime/
  tools/
  mcp/
  prompts/
```

继续保留：

```text
server/src/runtime/voice_runtime.py
server/src/backend_task_core/
server/src/task/
```

原则：

1. `agent-core` 只负责决策与能力调用。
2. `voice-runtime` 负责语音边界。
3. `backend-task-core` 作为与 `agent-core` 平级的独立模块，负责长期任务运行时。

---

## 15. 实施优先级

建议实施顺序：

1. 先接入 OpenAI Agents SDK，替换自研主循环
2. 再落第一批 Tool
3. 再落 `timer_task`
4. 然后接图片能力
5. 最后接导航能力

---

## 16. 最终设计结论

1. 当前项目采用 OpenAI Agents SDK 作为 `agent-core` 的运行时基座。
2. `VoiceSessionController` 保留，但仅作为语音边界组件。
3. `server-api`、`voice-runtime`、设备链路、媒体链路和 `task runtime` 继续自研。
4. `Tool / Skill / MCP / Task` 严格区分，其中短期 Skill 简化为业务型高级 Tool。
5. 当前整个方案中，风险最高的部分是 `task runtime`，实现优先级高于复杂 Skill 设计。
6. OpenAI Agents SDK 支持打断、引导、审批和任务协作中的决策层能力，但不替代本项目的业务引导层和任务执行层。
7. 通过统一的 AgentFacade、Tool 注册规范、Task Runtime 和 MCP 接入规范，可以持续支持更多开发者参与模型能力扩展。
