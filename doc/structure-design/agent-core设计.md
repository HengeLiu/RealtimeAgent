# agent-core 设计

## 1. 文档定位

本文档是当前项目 `agent-core` 的架构设计定稿。

本文档只保留已经收敛后的架构结论、模块边界、运行时对象、工具模型和与 `backend-task-core` 的协作关系，不再展开调研过程和选型对比。

方案调研与选型说明见：

- [agent-core方案调研与OpenAI Agents SDK选型说明.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/experimental/agent-core方案调研与OpenAI%20Agents%20SDK选型说明.md)

后台任务运行时的详细设计见：

- [backend-task-core设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/backend-task-core设计.md)

---

## 2. 设计目标

本设计用于支撑以下目标：

1. 在当前语音主链路之上，落地完整 `agent-core`。
2. 统一模型可调用能力面，避免在语音场景中暴露多套不同抽象。
3. 把 `MCP`、`Skill`、`Task` 都收敛到统一 `Tool` 体系中。
4. 为后续拍照解读、导航和后台任务管理提供稳定的承载框架。
5. 允许开发者扩展能力时优先新增 Tool，而不是修改主循环。

---

## 3. 最终设计结论

### 3.1 总体结论

当前项目采用如下策略：

1. `agent-core` 的通用运行循环基于 **OpenAI Agents SDK** 实现。
2. 模型侧只感知一套统一的 `function tools`。
3. `Tool` 是唯一模型可调用的能力抽象。
4. `Skill`、`MCP`、`Task` 不再作为模型侧独立概念暴露。
5. `backend-task-core` 继续作为独立后台任务运行时存在。

### 3.2 核心边界

#### OpenAI Agents SDK 承担

1. `agent loop`
2. function calling
3. 对话轮运行
4. 结构化输出约束
5. 模型与工具之间的标准调用协议

#### 项目继续自研

1. `server-api`
2. `voice-runtime`
3. 工具注册规范与调度规范
4. 图片抓拍链路
5. 媒体资产存储
6. `backend-task-core`
7. 任务模板与任务状态机
8. 设备执行策略

---

## 4. 总体架构

服务器侧采用如下分层：

1. `server-api`
2. `voice-runtime`
3. `agent-core`
4. `backend-task-core`
5. `tool layer`
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
   - 调用某个 Tool
4. 调用统一工具注册器中注册的 Tool。
5. 把工具调用结果写回上下文。
6. 统一处理错误和最终回复生成。

不负责：

1. 不直接维护设备连接。
2. 不直接管理媒体流。
3. 不直接维护后台任务状态机。

### 4.4 `backend-task-core`

职责：

1. 创建任务实例。
2. 管理任务状态机。
3. 调度任务生命周期。
4. 输出任务事件。
5. 接受查询、取消、暂停、恢复请求。
6. 在后台执行过程中调用需要的本地能力与外部能力。

不负责：

1. 不负责开放式对话决策。
2. 不负责自然语言理解。

### 4.5 `tool layer`

`tool layer` 是 `agent-core` 的统一能力层。

核心原则：

1. 对模型来说，只有 Tool，不再区分 Skill、MCP、Task。
2. 对项目代码来说，所有能力都收敛到单一 `BaseTool` 继承体系。
3. 所有 Tool 都进入同一个注册器。
4. 所有 Tool 都由同一个调度器透明执行。
5. 所有 Tool 都遵循同一套命名规范、参数规范和返回规范。

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
3. `agent-core` 基于统一 tools 做决策并返回：
   - 最终文本回复
   - 或需要追问的问题
   - 或在当前轮已完成的工具调用结果
4. `voice-runtime` 负责音频播报和设备控制。

---

## 6. 统一 Tool 模型

### 6.1 核心定义

`Tool` 是当前项目中唯一模型可调用的能力单元。

每个 Tool 本质上都是一个本地可运行函数。

这个函数：

1. 具有稳定工具名。
2. 具有稳定参数 schema。
3. 可以被注册到统一工具注册器。
4. 可以被注入到 OpenAI Agents SDK 的 tools 列表中。
5. 被大模型直接感知并按 function call 方式调用。

### 6.2 Tool 继承体系

当前项目采用单一 `BaseTool` 继承体系：

1. `BaseTool`
2. `BaseSkillTool`
3. `BaseMCPTool`
4. `BaseTaskTool`

约束如下：

1. `Function Tool` 直接继承 `BaseTool`。
2. `Skills Tool` 继承 `BaseSkillTool`。
3. `MCP Tool` 继承 `BaseMCPTool`。
4. `Task Tool` 继承 `BaseTaskTool`。
5. 任何模型可见能力最终都必须是 `BaseTool` 的子类。

### 6.3 四类 Tool

当前项目中的 Tool 一共分为四类：

1. `Function Tools`
2. `MCP Tools`
3. `Skills Tools`
4. `Task Tools`

这四类 Tool 只是在实现方式上不同，对模型暴露时没有本质区别。

### 6.4 统一执行契约

所有 `BaseTool` 子类都必须至少定义两个成员：

1. `spec`
2. `run`

其中：

1. `spec` 负责声明工具名称、说明、输入输出模型和标签。
2. `run` 负责执行实际逻辑并返回统一结果。

这意味着：

1. 注册器只认 `spec`。
2. 调度器只认 `run`。
3. 调度过程不区分 `Function / Skill / MCP / Task` 子类。

### 6.5 Function Tools

`Function Tools` 是最普通的本地函数工具。

特点：

1. 内部直接执行本地 Python 代码。
2. 用于简单增删改查、数据处理、状态读取或设备侧轻量能力。
3. 不依赖 Skill 运行时。
4. 不依赖 MCP Server。
5. 不直接承载后台任务管理职责。

示例：

1. `query_device_state`
2. `capture_photo`

### 6.6 MCP Tools

`MCP Tools` 也是本地函数工具，但其内部依赖某个 MCP Server。

特点：

1. 对模型表现为普通 function tool。
2. 函数内部负责根据参数调用 MCP Server。
3. 函数内部负责参数转换、超时、错误包装和结果整理。
4. MCP Server 不直接暴露给模型。

示例：

1. `amap_poi_search`
2. `amap_geocode`
3. `amap_route_plan`

### 6.7 Skills Tools

`Skills Tools` 也是本地函数工具。

特点：

1. 对模型表现为普通 function tool。
2. 工具类本身直接承载内部复合流程。
3. 不再要求维护第二套模型侧不可见的调用面。
4. 适合封装相对固定、可复用但不希望暴露细节的工作流。
5. 对调度器来说，它和其他 Tool 没有区别。

示例：

1. `photo_interpret`
2. `timer_manage`
3. 后续的 `prepare_navigation`

### 6.8 Task Tools

`Task Tools` 是与后台任务管理相关的一组本地函数工具。

特点：

1. 对模型表现为普通 function tool。
2. 函数内部直接调用 `backend-task-core` 的 `TaskManager` 或等价任务服务。
3. 默认只承载少数通用管理动作，例如创建、查询、取消、追加信息。
4. 模型不直接感知 `TaskRuntime` 内部状态机细节。

示例：

1. `create_task`
2. `query_task_status`
3. `cancel_task`
4. 后续的 `append_task_input`
5. 后续的 `pause_task`
6. 后续的 `resume_task`

### 6.9 Task 与 TaskRuntime 的定位

`Task` 和 `TaskRuntime` 仍然是项目内部重要概念，但不再属于模型直接调用的扩展单元。

定位如下：

1. `Task` 是后台任务模板。
2. `TaskRuntime` 是某个任务被创建后的实例。
3. 模型通过少数通用 `Task Tools` 管理任务，而不是直接调用 `Task`。
4. Task 方向的主要扩展点是新增 `Task` 模板，而不是不断新增专用 `Task Tool`。
5. `backend-task-core` 负责 `Task` 和 `TaskRuntime` 的注册、创建、调度和托管。

---

## 7. Tool 统一约束

### 7.1 命名规范

所有 Tool 都遵循统一命名规范：

1. 使用稳定英文名。
2. 使用 `verb_object` 风格。
3. 对模型暴露的名称必须唯一。
4. 同一能力对模型只保留一个主名称，避免同义重复暴露。

推荐示例：

1. `query_device_state`
2. `capture_photo`
3. `photo_interpret`
4. `amap_route_plan`
5. `create_task`

### 7.2 参数规范

所有 Tool 都遵循统一参数规范：

1. 使用统一 schema 定义输入参数。
2. 参数命名保持稳定。
3. 同类工具优先复用相同字段命名。
4. 不允许把大段自然语言工作流说明直接塞进参数中。

### 7.3 返回规范

所有 Tool 都遵循统一返回规范：

1. 返回结构化结果对象。
2. 需要时附带 `asset_refs`。
3. 需要时附带 `derived_artifacts`。
4. 需要时附带 `task_refs`。
5. 错误返回统一错误结构。

### 7.4 注册规范

所有 Tool 都必须：

1. 注册到同一个 `ToolRegistry`。
2. 通过 `spec` 自动生成注册信息。
3. 通过同一种方式暴露为 OpenAI Agents SDK function tool。
4. 由同一个 `ToolDispatcher` 或 `ToolRouter` 执行。
5. 由同一套逻辑负责参数校验、错误包装、轨迹记录和结果回写。

补充约束：

1. 开发者不需要手写逐个注册子类的逻辑。
2. 注册器应自动发现 `BaseTool` 子类并完成注册。
3. 调度器对所有 `BaseTool` 子类一视同仁，只通过 `spec` 和 `run` 工作。

---

## 8. OpenAI Agents SDK 下的映射关系

### 8.1 唯一模型调用面

当前项目在 OpenAI Agents SDK 下只暴露一种原生能力：

1. function tools

这意味着：

1. `Function Tools` 直接映射为 SDK function tools。
2. `MCP Tools` 是内部调用 MCP Server 的 SDK function tools。
3. `Skills Tools` 是内部封装复合流程的 SDK function tools。
4. `Task Tools` 是内部调用任务管理服务的 SDK function tools。

### 8.2 不直接采用的模型侧抽象

在当前语音场景中，以下概念不作为模型侧独立抽象暴露：

1. `Skill`
2. `MCP Server`
3. `Task`
4. `TaskRuntime`

### 8.3 这样设计的原因

原因如下：

1. 语音对话场景更强调稳定和收敛，不适合暴露过多可变工作流抽象。
2. 模型侧抽象越少，工具选择越稳定。
3. OpenAI Agents SDK 原生最稳定的承载形式就是 function tools。
4. `Skill`、`MCP`、`Task` 更适合作为项目内部实现细节，而不是模型心智的一部分。

---

## 9. 上下文与资产模型

完整 `agent-core` 保留并扩展以下模型：

1. `MessageContext`
2. `MediaAssetRef`
3. `DerivedArtifact`
4. `CapabilityTrace`
5. `TaskRef`

### 9.1 MessageContext

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

### 9.2 MediaAssetRef

至少包含：

1. `asset_id`
2. `asset_type`
3. `storage_uri`
4. `mime_type`
5. `codec`
6. `duration_ms / width / height / fps`
7. `source_stream_id`

### 9.3 DerivedArtifact

用于保存：

1. 语音转写文本
2. 图片摘要
3. 地图查询结果
4. 路线摘要
5. 检测结果
6. 任务查询结果

### 9.4 TaskRef

用于表示会话与后台任务的关联关系。

### 9.5 CapabilityTrace

用于记录一次 turn 中的工具调用轨迹。

原则：

1. 顶层面向模型的调用轨迹统一按 Tool 视角记录。
2. 若内部还需要记录 Skill、MCP、Task 子步骤，应作为内部子轨迹或调试信息存在。
3. 不应让主链路形成多套平级调用面。

---

## 10. 最小运行时对象

完整 `agent-core` 至少包含以下对象：

1. `AgentSession`
2. `AgentTurn`
3. `DialogState`
4. `CapabilityTrace`
5. `TaskRef`
6. `VoiceSessionController`

### 10.1 AgentSession

表示一条开放式会话。

### 10.2 AgentTurn

表示一次输入处理单元。

输入来源包括：

1. 语音转写文本
2. 文本输入
3. 图片抓拍结果
4. 任务完成事件
5. 任务状态变化事件

### 10.3 DialogState

用于表示：

1. 当前待确认信息
2. 当前待补齐参数
3. 当前追问状态

---

## 11. task runtime 设计

这是当前方案里风险最高、优先级最高的部分。

本章只保留系统级摘要，`backend-task-core` 的详细设计以 [backend-task-core设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/backend-task-core设计.md:1) 为准。

### 11.1 定位

`task runtime` 即与 `agent-core` 平级的 `backend-task-core`。

其系统定位是：

1. 统一承接所有长生命周期后台任务。
2. 作为 `Task` 模板的注册、创建、调度和托管中心。
3. 独立于 `agent-core` 运行，但与 `agent-core` 通过标准事件和任务网关协作。

### 11.2 系统边界

`agent-core` 负责：

1. 理解用户意图。
2. 决定是否调用 `Task Tools`。
3. 在对话执行链路中调用统一 Tool。
4. 在收到任务事件后决定是否追问、确认、播报或继续调用其他 Tool。

`backend-task-core` 负责：

1. 注册和发现 `Task` 模板。
2. 创建并托管 `TaskRuntime` 实例。
3. 管理任务状态机、调度、上下文和事件流。
4. 对外暴露任务管理服务，供 `Task Tools` 调用。

### 11.3 协作原则

双方协作遵循以下原则：

1. 模型不能直接操作 `TaskRuntime` 内部对象。
2. 模型只能通过 `Task Tools` 管理任务。
3. `backend-task-core` 必须先产出结构化事件，再由统一通知策略决定是否直接通知设备。
4. 默认任务事件先回流 `agent-core`；高优先级或安全相关事件允许绕过 `agent-core` 直接通知，同时仍需回流标准事件用于上下文同步。

典型链路如下：

1. 用户输入进入 `agent-core`。
2. `agent-core` 调用某个 `Task Tool`。
3. `Task Tool` 调用 `backend-task-core` 的任务管理服务。
4. `TaskManager` 根据 `task_type` 启动对应 `Task` 模板并创建 `TaskRuntime`。
5. 任务运行过程中持续产出标准事件。
6. 事件根据优先级决定是仅回流 `agent-core`，还是先直达设备再回流 `agent-core`。

---

## 12. Agent Loop 设计

一轮 `AgentTurn` 的处理流程如下：

1. `TurnBuilder`
2. `ContextAssembler`
3. `Planner`
4. `DecisionRouter`
5. `ToolDispatcher`
6. `ContextUpdater`
7. 若未完成，则继续下一步

建议动作类型至少包括：

1. `final_answer`
2. `ask_user`
3. `call_tool`
4. `fail`

说明：

1. 创建任务、查询任务、取消任务都属于 `call_tool`。
2. 调用 MCP 能力也属于 `call_tool`。
3. 调用 Skill 也属于 `call_tool`。

---

## 13. 第 4-8 项的落地映射

### 13.1 第 4 项：AgentCore 调工具

落地项：

1. OpenAI Agents SDK 作为主循环。
2. `AgentFacade`。
3. 统一 `ToolRegistry`。
4. 统一 `ToolDispatcher`。
5. 第一批 Tool。

### 13.2 第 5 项：Skills 与 MCP

落地项：

1. 保留 Skill 内部实现，但通过 `Skills Tools` 暴露。
2. 保留 MCP 接入，但通过 `MCP Tools` 暴露。

首批建议：

1. `photo_interpret`
2. `timer_manage`
3. `amap_poi_search`
4. `amap_geocode`
5. `amap_route_plan`

### 13.3 第 6 项：拍照 Skill + 图片解读

设计：

1. `capture_photo` 作为 `Function Tool`。
2. `photo_interpret` 作为 `Skills Tool`。
3. 图片抓拍和资产存储继续自研。

### 13.4 第 7 项：AMap MCP 导航

设计：

1. AMap 作为内部 MCP Server 或 MCP Adapter 来源。
2. `amap_poi_search`、`amap_geocode`、`amap_route_plan` 作为 `MCP Tools`。
3. 后续导航长期执行由任务系统承接时，也仍通过 Tool 进行管理。

### 13.5 第 8 项：后台任务管理

设计：

1. `create_task`
2. `query_task_status`
3. `cancel_task`
4. `timer_task`

原则：

1. Agent 负责决策。
2. Task Runtime 负责执行。
3. 模型通过少数通用 `Task Tools` 管理任务。
4. Task 方向的主要扩展点是新增 `Task` 模板。

---

## 14. 扩展规范

### 14.1 新增 Function Tool

需要完成：

1. 实现 `BaseTool` 子类。
2. 中文 docstring。
3. 定义统一输入输出 schema。
4. 通过 `spec` 被统一 `ToolRegistry` 自动发现。
5. 补测试。

### 14.2 新增 MCP Tool

需要完成：

1. 实现 `BaseMCPTool` 子类。
2. 在 `run()` 内部调用 MCP Server。
3. 做参数转换、超时控制和错误包装。
4. 通过 `spec` 被统一 `ToolRegistry` 自动发现。
5. 补测试。

### 14.3 新增 Skills Tool

需要完成：

1. 实现 `BaseSkillTool` 子类。
2. 在 `run()` 内部实现对应复合流程。
3. 对内部执行结果做统一整理。
4. 通过 `spec` 被统一 `ToolRegistry` 自动发现。
5. 补测试。

### 14.4 新增 Task Tool

需要完成：

1. 实现 `BaseTaskTool` 子类。
2. 在 `run()` 内部调用任务管理服务。
3. 返回统一任务结果或 `TaskRef`。
4. 通过 `spec` 被统一 `ToolRegistry` 自动发现。
5. 补测试。

### 14.5 新增 Task 模板

需要完成：

1. 实现 `Task` 模板代码。
2. 定义输入、上下文和状态机。
3. 在 `backend-task-core` 的任务注册器中注册。
4. 补充对应 `Task Tools`，而不是把 `Task` 直接暴露给模型。

---

## 15. 推荐目录结构

建议后续目录结构如下：

```text
server/src/agent_core/
  facade/
  context/
  runtime/
  tools/
  prompts/
```

其中 `tools/` 内部可以继续按实现类型拆分，例如：

```text
server/src/agent_core/tools/
  base.py
  registry.py
  dispatcher.py
  function_tools/
  mcp_tools/
  skill_tools/
  task_tools/
```

继续保留：

```text
server/src/runtime/voice_runtime.py
server/src/backend_task_core/
```

原则：

1. `agent-core` 只负责决策与工具调用。
2. `voice-runtime` 负责语音边界。
3. `backend-task-core` 作为与 `agent-core` 平级的独立模块，负责长期任务运行时。

---

## 16. 实施优先级

建议实施顺序：

1. 先接入 OpenAI Agents SDK，替换自研主循环。
2. 先统一 `BaseTool` 继承体系、`ToolRegistry` 与 `ToolDispatcher`。
3. 再把现有 Function、Skill、MCP、Task 管理能力全部收口到 Tool 模型。
4. 再打通 `timer_task`。
5. 然后接图片能力。
6. 最后接导航能力。

---

## 17. 最终设计结论

1. 当前项目采用 OpenAI Agents SDK 作为 `agent-core` 的运行时基座。
2. `VoiceSessionController` 保留，但仅作为语音边界组件。
3. `server-api`、`voice-runtime`、设备链路、媒体链路和 `backend-task-core` 继续自研。
4. 模型侧只感知统一 Tool，不再直接感知 `Skill`、`MCP`、`Task`。
5. 当前全部能力统一抽象为四类 Tool：`Function Tools`、`MCP Tools`、`Skills Tools`、`Task Tools`。
6. 所有 Tool 必须继承自 `BaseTool` 体系，遵循统一 `spec + run` 契约，并由同一个注册器自动发现、由同一个调度器透明执行。
7. `Task` 与 `TaskRuntime` 仍然是内部关键概念，但只通过 `Task Tools` 对模型提供管理能力。
