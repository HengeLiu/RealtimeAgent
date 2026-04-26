# agent-core 设计

## 1. 文档定位

本文档是当前项目 `agent-core` 的正式设计文档。

本文档只保留当前已经收敛后的架构结论、职责边界、运行流程和扩展约束，不再保留已经被推翻的中间方案。

相关文档：

- [agent-core方案调研与OpenAI Agents SDK选型说明.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/experimental/agent-core方案调研与OpenAI%20Agents%20SDK选型说明.md)
- [backend-task-core设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/backend-task-core设计.md)

---

## 2. 设计目标

`agent-core` 的目标是：

1. 在当前语音主链路之上承接多轮对话与工具调用。
2. 使用 OpenAI Agents SDK 作为标准 `agent loop` 与工具调用基座。
3. 让模型只处理自然语言理解、工具选择和工具结果后的回复生成。
4. 让上下文管理、媒体管理、任务管理、设备协议都由框架负责，不泄漏给模型。
5. 为拍照解读、地图导航、计时器任务提供稳定扩展面。

---

## 3. 本次回顾后的修正结论

本次重新回顾设计后，明确修正以下不合理点：

1. **不再定义模型侧自有 `action` 协议。**
   - `final_answer / ask_user / call_tool` 这类概念不再作为模型输出协议设计。
   - 工具调用完全依赖 OpenAI Agents SDK 的原生 tool calling。
2. **不再让模型感知架构概念。**
   - 例如“资产”“派生结果”“运行阶段”“TaskRuntime”都属于框架概念，不进入模型提示词。
3. **不再把底层原子能力直接暴露给模型。**
   - 模型只看到少量高层工具。
4. **不再把设计写成一套自研 Agent Runtime。**
   - `Planner`、`DecisionRouter`、自定义动作协议等不再作为正式设计要求。
5. **上下文由框架管理，不交给模型决定。**
   - 历史消息保留、顺序、裁剪、模态输入组织都属于框架职责。

---

## 4. 最终设计结论

### 4.1 总体结论

当前项目采用如下策略：

1. `agent-core` 的通用主循环基于 **OpenAI Agents SDK**。
2. 模型侧只感知一组统一工具。
3. 模型不感知 `MCP`、`TaskRuntime` 等内部概念。
4. `backend-task-core` 继续作为与 `agent-core` 平级的后台任务运行时。
5. `voice-runtime` 继续负责语音边界，不由 SDK 替代。

### 4.2 OpenAI Agents SDK 承担

1. `agent loop`
2. tool calling
3. 多轮工具调用过程控制
4. 模型与工具之间的标准协议
5. 结构化工具参数约束

### 4.3 项目继续自研

1. `server-api`
2. `voice-runtime`
3. 设备协议与控制消息
4. 抓拍链路与媒体落盘
5. 会话上下文存储
6. `backend-task-core`
7. 通知与播报策略

---

## 5. 总体架构

服务器侧采用如下分层：

1. `server-api`
2. `voice-runtime`
3. `agent-core`
4. `backend-task-core`
5. `device/media adapters`
6. `context store`

### 5.1 `server-api`

职责：

1. 接收控制连接与媒体连接。
2. 维护 `/ws/control`、`/ws_audio`、`/stream.wav` 等接入点。
3. 路由设备输入到对应运行时。

不负责：

1. 不负责模型决策。
2. 不负责任务状态机。

### 5.2 `voice-runtime`

职责：

1. 管理语音会话。
2. 聚合用户单轮音频。
3. 调用 ASR。
4. 把音频输入转换为 `AgentTurn`。
5. 把最终文本回复转换为音频并控制播放。

不负责：

1. 不负责业务工具选择。
2. 不负责长期任务调度。

### 5.3 `agent-core`

职责：

1. 维护多轮会话上下文。
2. 调用 OpenAI Agents SDK 执行一轮对话。
3. 向 SDK 注入当前模型可见工具。
4. 把工具结果写回会话。
5. 产出当前轮回复文本。

不负责：

1. 不直接管理设备连接。
2. 不直接管理媒体流。
3. 不直接维护后台任务生命周期。

### 5.4 `backend-task-core`

职责：

1. 创建任务实例。
2. 保存任务上下文。
3. 驱动任务状态机。
4. 输出结构化任务事件。
5. 接受任务控制命令。

不负责：

1. 不做自然语言理解。
2. 不直接替代 `agent-core`。

---

## 6. 模型职责与框架职责

这是当前设计里最重要的边界。

### 6.1 模型职责

模型只负责：

1. 理解用户自然语言输入。
2. 判断是否需要调用工具。
3. 在工具返回后生成面向用户的最终文本回复。

### 6.2 框架职责

框架负责：

1. 管理历史消息。
2. 组织多模态输入。
3. 管理媒体文件、路径、会话对象和任务对象。
4. 把底层能力注册成模型可见工具。
5. 保存工具结果、任务结果和调试轨迹。

### 6.3 明确不应交给模型的内容

以下内容不应写入提示词让模型理解：

1. “资产”“派生结果”“TaskRuntime”“MCP 方法”这类内部术语。
2. 历史消息该保留多少条。
3. 媒体落盘路径。
4. 任务状态机细节。
5. 应用层内部控制协议。

---

## 7. 模型输入原则

### 7.1 历史消息

历史对话直接使用框架维护的原始 `history messages`：

1. 保留真实 `user / assistant` 顺序。
2. 默认不自行压缩成说明文本。
3. 是否裁剪由框架决定，不由模型决定。

### 7.2 文本输入

语音链路进入模型前，应先经过 ASR，模型默认接收文本，而不是音频路径。

### 7.3 图片输入

当需要使用用户眼前图像时：

1. 先通过拍照工具触发抓拍。
2. 再通过 SDK 原生图片输入把图片送给模型。
3. 不把图片路径写进提示词代替图片输入。

### 7.4 system prompt

system prompt 只保留：

1. 助手身份。
2. 语言风格。
3. 必要安全边界。
4. 需要工具时的最小使用原则。

system prompt 不承载：

1. 架构分层知识。
2. 框架运行规则。
3. 自定义动作协议。

---

## 8. 工具设计

### 8.1 核心原则

对模型来说，只有一层统一工具。

对工程内部来说，可以继续保留：

1. 本地函数能力
2. 复合业务能力
3. MCP 适配层
4. 任务网关

但这些都属于内部实现，不是模型心智。

### 8.2 当前模型可见工具

当前模型侧只暴露 3 个高层工具：

1. `capture_photo`
2. `timer_manage`
3. `map_manage`

说明：

1. `capture_photo` 只负责“需要看图时先拍照拿到当前画面”。
2. `timer_manage` 负责“创建、查询、取消计时器”。
3. `map_manage` 负责“地点搜索、地址解析、路线规划”。
4. 若同一轮内连续生成多条助手语音回复，播放层必须按顺序排队播报，不能让后续回复覆盖前序播放。

### 8.3 当前内部能力

内部保留但默认不直接暴露给模型的能力包括：

1. `create_timer`
2. `query_task_status`
3. `cancel_task`
4. `amap.poi_search`
5. `amap.geocode`
6. `amap.route_plan`

### 8.4 设计理由

这样设计的原因是：

1. 模型看到的工具越少，选择越稳定。
2. 用户说的是“帮我看看前面有什么”“帮我定时 5 分钟”“导航去最近的咖啡店”，模型只需要看见与这些语义直接对应的高层入口。
3. 拍照和图片理解要拆开：工具只负责获取真实图片，图片解读由主链路模型自己完成。
4. 当视觉链路耗时较长时，框架可以在工具调用前后插入中间播报，减少长时间静默等待。
5. 拍照完成后的图片解读要切换到专门的“看图直接回答”提示词，避免模型只说明已经拍照或追问保存照片。
6. 每次模型决定再次调用 `capture_photo` 时，主链路只允许使用本次新抓拍的图片，不允许回退复用历史旧图。

---

## 9. 当前实现对象

当前正式设计只要求以下核心对象：

1. `AgentFacade`
2. `AgentSessionStore`
3. `OpenAIAgentLoopRunner`
4. `ToolRegistry`
5. `ToolGateway`
6. `McpRegistry / McpGateway`

### 9.1 `AgentFacade`

职责：

1. 接收 `AgentTurn`
2. 调用主循环
3. 把结果写回会话

### 9.2 `AgentSessionStore`

职责：

1. 保存消息
2. 保存媒体引用
3. 保存任务引用
4. 保存能力调用轨迹

### 9.3 `OpenAIAgentLoopRunner`

职责：

1. 组装最小输入
2. 调用 OpenAI Agents SDK
3. 收集工具调用结果

它不是自研 agent loop，只是 SDK 的项目适配器。

### 9.4 `ToolRegistry`

职责：

1. 管理全部内部能力
2. 管理模型可见工具子集
3. 统一导出给 SDK

---

## 10. 上下文与对象模型

完整 `agent-core` 保留以下对象：

1. `MessageContext`
2. `MediaAssetRef`
3. `DerivedArtifact`
4. `CapabilityTrace`
5. `TaskRef`
6. `AgentSession`
7. `AgentTurn`

### 10.1 这些对象的定位

这些对象是**框架内部对象**，用于：

1. 保存状态
2. 支撑调试
3. 支撑联调
4. 支撑后续任务与媒体回流

它们不是模型提示词的一部分。

### 10.2 `CapabilityTrace`

`CapabilityTrace` 只用于：

1. 调试
2. 日志
3. 回归结果分析

不用于给模型讲解运行过程。

---

## 11. 与 backend-task-core 的边界

### 11.1 `agent-core` 负责

1. 理解用户是否要创建、查询或取消任务。
2. 通过 `timer_manage` 等工具调用任务网关。
3. 在任务事件回流后继续对话。

### 11.2 `backend-task-core` 负责

1. 任务实例生命周期。
2. 任务状态机。
3. 定时调度和超时。
4. 结构化任务事件。

### 11.3 协作原则

1. 模型不直接操作 `TaskRuntime`。
2. 模型只通过高层工具间接管理任务。
3. `backend-task-core` 发布结构化事件，再由上层决定是否播报。

---

## 12. 当前不再采用的设计

以下设计已确认不合理，不再作为正式架构要求：

1. 自定义模型输出 `action=final_answer / ask_user / call_tool`
2. 让模型输出“工具动作 JSON”
3. `Planner -> DecisionRouter -> ToolDispatcher` 这类自研 loop 分层
4. 把“资产”“派生结果”“运行阶段”等内部词汇写入模型提示词
5. 把底层原子能力大面积直接暴露给模型

---

## 13. 第 4-8 项的设计映射

### 13.1 第 4 项：AgentCore 调工具

落地项：

1. OpenAI Agents SDK 作为主循环
2. `AgentFacade`
3. `ToolRegistry`
4. `ToolGateway`

### 13.2 第 5 项：工具与 MCP

落地项：

1. 保留高层 Tool 内部实现
2. 保留 MCP 内部适配
3. 模型侧通过高层工具统一调用

### 13.3 第 6 项：拍照工具 + 图片解读

设计：

1. `capture_photo` 作为模型可见拍照工具
2. 图片通过 SDK 原生图片输入传给主链路模型
3. 本期不再保留 `photo_interpret` 作为正式能力抽象

### 13.4 第 7 项：AMap 导航

设计：

1. AMap 作为内部 MCP 来源
2. `map_manage` 作为模型可见地图工具
3. `amap.*` 作为内部方法

### 13.5 第 8 项：后台任务管理

设计：

1. `timer_manage` 作为模型可见高层任务工具
2. `backend-task-core` 继续承接长期任务运行时

---

## 14. 扩展规范

### 14.1 新增模型可见工具

只有在满足以下条件时，才允许新增模型可见工具：

1. 对用户语义是稳定且高层的能力。
2. 不是底层原子接口。
3. 不会明显增加模型选择负担。

### 14.2 新增内部能力

新增内部能力时：

1. 可以是本地函数能力。
2. 可以是 MCP 方法。
3. 可以是任务控制能力。
4. 默认先不直接暴露给模型。

### 14.3 新增任务模板

新增长期任务时：

1. 任务模板注册到 `backend-task-core`
2. 若需要模型管理，再通过高层工具暴露

---

## 15. 推荐目录结构

建议在 SDK 主体实现中保持：

```text
openaiglass-sdk/server-python/agent_core/
  facade/
  context/
  runtime/
  tools/
  mcp/
```

以及：

```text
openaiglass-sdk/server-python/runtime/voice_runtime.py
openaiglass-sdk/server-python/backend_task_core/
```

原则：

1. `agent-core` 只承接 SDK 适配、会话与工具管理。
2. `voice-runtime` 只承接语音边界。
3. `backend-task-core` 只承接长期任务运行时。

---

## 16. 最终设计结论

1. 当前项目采用 OpenAI Agents SDK 作为 `agent-core` 的运行时基座。
2. 模型不再使用项目自定义 `action` 协议。
3. 工具调用完全依赖 SDK 原生 tool calling。
4. 模型只看到 3 个高层工具：`capture_photo / timer_manage / map_manage`。
5. 框架负责上下文、媒体、任务、调试轨迹和设备协议，不把这些概念泄漏给模型。
6. `backend-task-core` 与 `agent-core` 保持平级独立。
