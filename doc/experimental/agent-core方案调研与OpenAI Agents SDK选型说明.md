# agent-core 方案调研与 OpenAI Agents SDK 选型说明

## 1. 文档定位

本文档用于保存 `agent-core` 方案调研、选型讨论、适配性分析和解释性内容。

本文档不是最终设计定稿，不直接作为实现约束文件。

最终设计定稿见：

- [agent-core设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/agent-core设计.md)

---

## 2. 调研目标

本次调研重点回答：

1. 当前项目是否有必要完全自研 `agent-core`。
2. 如果引入开源运行时，哪些能力可以直接复用。
3. OpenAI Agents SDK 对当前项目的适配性如何。
4. OpenAI Agents SDK 支持哪些“打断”“引导”“审批”“任务协作”能力。
5. 哪些地方仍然必须保留项目自研。

---

## 3. 当前项目的真实需求

当前项目不是一个普通聊天机器人，而是一个三端协同系统。

它的核心特征包括：

1. 有眼镜、服务器、手机三端协作。
2. 有语音输入、语音播放、图片抓拍、视频流等多模态链路。
3. 有明显的设备执行层和感知层。
4. 后续存在长期运行任务，如计时器、导航、手机直连视频链路。
5. 既需要开放式对话，又需要严格结构化执行。

因此，当前项目里的 `agent-core` 需要同时满足两类诉求：

1. 通用 Agent Runtime 诉求
   - loop
   - tools
   - sessions
   - mcp
   - tracing
2. 项目专有 Runtime 诉求
   - 设备接入
   - 语音链路
   - 长期任务运行时
   - 设备执行策略

---

## 4. 为什么不建议继续完全自研 agent-core

继续完全自研的问题主要有：

1. 自己维护 `agent loop` 成本高。
2. 会话、追踪、工具调用和 MCP 适配都容易重复造轮子。
3. 随着能力增多，核心运行时容易不断膨胀。
4. 后续新开发者参与时，需要先理解大量自定义运行时细节，扩展门槛高。

因此，更合理的策略是：

1. 把通用 Agent Runtime 交给成熟框架。
2. 把项目独有的设备层、任务层和媒体层留给自研。

---

## 5. 为什么选择 OpenAI Agents SDK

## 5.1 选择理由

选择 OpenAI Agents SDK 的主要原因如下：

1. 当前行业大量模型服务在接口层向 OpenAI 风格对齐。
2. OpenAI Agents SDK 已经提供：
   - `agent loop`
   - tool calling
   - sessions
   - MCP
   - tracing
   - handoff
   - human-in-the-loop
3. 它更适合作为通用 `agent-core` 基座，而不是要求项目继续维护一套完全自定义的 loop。

## 5.2 适配性判断

基于当前项目需求，适配性判断如下：

1. 第 4 项：高
2. 第 5 项：MCP 高，Skill 中
3. 第 6 项：高
4. 第 7 项：中高
5. 第 8 项：中

原因在于：

1. 通用 agent 决策和工具调用能力适配很好。
2. 长生命周期任务运行时不是它的设计重点。

---

## 6. OpenAI Agents SDK 支持能力分析

## 6.1 支持的能力

OpenAI Agents SDK 已提供：

1. `Runner.run / run_streamed`
2. `function tools`
3. `sessions`
4. `mcp`
5. `tracing`
6. `handoff`
7. `human-in-the-loop`

这些能力能很好承接当前项目里最通用的一层：

1. 主循环
2. 工具调用
3. MCP 调用
4. 会话历史
5. 可观测性

## 6.2 不适合直接承接的能力

OpenAI Agents SDK 不适合作为以下模块的替代品：

1. `server-api`
2. `voice-runtime`
3. 媒体链路
4. 图片抓拍链路
5. `backend-task-core`
6. 任务状态机

原因：

1. 这些部分都明显带有当前项目的设备系统特征。
2. 它们不属于通用 Agent Runtime 的职责范围。

---

## 7. 对打断、引导、审批和任务协作的支持边界

## 7.1 对打断的支持

### 7.1.1 语音打断

OpenAI Agents SDK 的 Realtime 能力支持语音对话中的打断和播放跟踪。

从能力边界看，这意味着：

1. 它支持“用户在助手播放过程中打断”的场景。
2. 它适合未来评估实时语音对话方案。

但当前项目已经有自己的设备语音链路，因此短期不建议直接用它替换现有 `voice-runtime`。

### 7.1.2 工具审批中断

OpenAI Agents SDK 支持 human-in-the-loop。

它适合处理：

1. 敏感工具调用前确认。
2. 暂停执行。
3. 审批后恢复执行。

## 7.2 对引导的支持

### 7.2.1 流程引导

OpenAI Agents SDK 支持：

1. `handoff`
2. `guardrails`
3. approval

因此，它支持的是：

1. Agent 流程分流。
2. 输入输出约束。
3. 需要确认的动作。

### 7.2.2 业务引导

它不直接提供当前项目中的：

1. 导航引导
2. 计时提醒策略
3. 设备执行策略
4. 相机任务执行策略

这些仍然要依赖：

1. prompt
2. Tool/Skill
3. task runtime
4. 设备执行层

## 7.3 对任务协作的支持

它可以支持：

1. 是否创建任务的决策。
2. 查询任务状态的决策。
3. 基于任务事件继续对话。

它不能支持：

1. 任务调度器。
2. 任务状态机。
3. 长期运行实例托管。

因此，对任务协作的支持只到“决策层”，不到“执行层”。

---

## 8. Skill 为什么短期简化

当前项目短期内不会有大量复杂 Skill，因此建议主动控制复杂度。

原因：

1. 当前架构风险最大的是 `task runtime`。
2. 现在就引入复杂 Skill Runtime 会分散实现精力。
3. 当前阶段的大部分 Skill，本质上都可以先做成“带固定流程的高级 Tool”。

因此短期建议：

1. `Skill = 业务型高级 Tool`
2. 等后续复杂能力明显增多，再升级成更正式的一层

---

## 9. 为什么 task runtime 必须继续自研

这是整个方案里最需要明确的一点。

原因：

1. 任务生命周期跨越单次模型调用。
2. 任务状态不能只存在模型上下文中。
3. 任务需要异步执行、超时、取消和完成通知。
4. 导航、计时器、手机直连视频等能力都属于长期任务。

如果把这些都塞进 Agent Runtime，会导致：

1. `agent-core` 重新膨胀。
2. 任务状态难以清晰管理。
3. 任务与对话边界再次混乱。

因此，`backend-task-core` 必须继续保留，而且必须作为独立运行时中心被强化。

---

## 10. 最终选型结论

本次调研的最终结论如下：

1. 当前项目不应继续完全自研通用 `agent-core`。
2. 当前项目应采用 OpenAI Agents SDK 作为通用 Agent Runtime 基座。
3. 当前项目应继续自研：
   - `server-api`
   - `voice-runtime`
   - `backend-task-core`
   - 设备和媒体链路
4. 当前项目短期内不做重型 Skill Runtime，Skill 先简化为业务型高级 Tool。
5. 后续实现优先级应优先保障：
   - OpenAI Agents SDK 接入
   - 第一批 Tool
   - `timer_task`
   - 图片能力
   - 导航能力

最终设计定稿见：

- [agent-core设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/agent-core设计.md)

