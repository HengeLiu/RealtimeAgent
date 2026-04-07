# Agent运行时与ToolSkill调度设计

## 1. 文档目标

本文定义服务器侧 `agent-core` 的运行模式、上下文管理方式、模型适配层、Tool/Skill 调度机制，以及与后台任务中心的协作边界。

当前默认技术决策：

- 第一阶段 `agent-core` 以 OpenAI Agents SDK 作为首选运行时工具
- 但对外仍通过本项目自定义的 `AgentRuntime`、`ToolRegistry`、`SkillGateway`、`TaskGateway` 等抽象暴露能力

本文重点解决：

- Agent 运行时承担哪些职责。
- 大模型输入输出如何进入系统。
- Tool 与 Skill 如何统一暴露给 Agent。
- 什么时候直接返回结果，什么时候创建后台任务。

## 2. Agent 运行时定位

`agent-core` 是服务器侧开放式交互任务中枢，负责：

- 承接用户自然语言输入
- 组织上下文
- 调用模型进行理解与决策
- 调用 Tool / Skill / MCP
- 在必要时创建后台任务
- 生成面向设备的响应

`agent-core` 不负责：

- 设备长连接管理
- 任务实例生命周期管理
- 具体硬件调用

## 3. 运行时总体结构

建议由以下组件组成：

- `AgentRuntime`
- `ConversationContextStore`
- `ModelAdapter`
- `ToolRegistry`
- `SkillGateway`
- `TaskGateway`
- `ResponsePlanner`

## 4. 主循环设计

建议主循环：

1. 接收用户输入事件
2. 构建上下文
3. 调用模型推理
4. 解析模型输出
5. 若需要工具调用，则调用 Tool / Skill / MCP
6. 若需要长任务，则调用 `TaskGateway`
7. 汇总结果
8. 生成对设备的回复或命令

## 5. 上下文模型设计

## 5.1 上下文组成

第一阶段上下文建议包含：

- 最近对话轮次
- 当前活跃任务摘要
- 当前设备状态摘要
- 当前绑定关系摘要
- 最近一次工具调用结果摘要

## 5.2 上下文分层

建议分为：

- `conversation_context`
- `runtime_context`
- `task_context_snapshot`

### `conversation_context`

用于模型理解当前对话。

### `runtime_context`

用于让模型了解当前系统可做什么、设备状态如何。

### `task_context_snapshot`

用于让模型了解已有任务，而不重复创建。

## 5.4 当前绑定关系摘要说明

“当前绑定关系摘要”是给 Agent 的一小段结构化运行时上下文，用来回答：

- 当前这副眼镜有没有绑定手机
- 绑定的是哪台手机
- 绑定关系当前是否有效
- 对应手机当前是否在线、是否具备目标能力

这里的“摘要”必须刻意保持短小，不应该把完整绑定表或大量设备详情直接塞进模型上下文。

建议只保留最小必要信息，例如：

```json
{
  "glass_device_id": "dev_glass_001",
  "bound_phone": {
    "device_id": "dev_phone_001",
    "binding_status": "active",
    "online_status": "online",
    "capabilities": ["video_stream_receive", "local_yolo"]
  }
}
```

不建议放入：

- 全量设备列表
- 历史绑定记录
- 大段日志
- 与当前决策无关的详细网络状态

这样设计的原因是：

- Agent 只需要知道“当前能不能调用手机侧能力”
- 不需要知道整个绑定系统的完整内部数据
- 可以减少上下文长度，降低模型噪音

## 5.3 上下文控制原则

- 第一阶段不做复杂长期记忆体系。
- 必须控制上下文增长。
- 工具结果只保留摘要，不把原始大数据直接塞进对话上下文。

## 6. 模型适配层设计

## 6.1 目标

- 屏蔽不同模型供应商差异
- 统一同步问答、流式输出、工具调用接口

## 6.2 建议抽象

- `ModelAdapter.generate()`
- `ModelAdapter.stream_generate()`
- `ModelAdapter.generate_with_tools()`

## 6.2.1 与首选运行时工具的关系

即使第一阶段默认以 OpenAI Agents SDK 作为首选运行时工具，模型适配层抽象仍然保留。

原因：

- 运行时工具与底层模型供应商不是同一层概念
- 本项目仍然可能继续使用 Qwen / 百炼作为模型提供方
- 需要避免未来切换运行时工具或模型供应商时大面积改动 `agent-core`

## 6.3 第一阶段建议实现

- `BailianQwenOmniAdapter`

需要支持：

- 非实时语音理解与回复
- 实时语音流式对话
- 结构化工具调用输出

## 7. Tool / Skill 统一暴露方式

## 7.1 基本原则

- 对 Agent 来说，Tool 是统一调用入口。
- Skill 是系统内部最常见的 Tool 类型。
- MCP 可以直接作为 Tool 暴露，也可以先封装为 Skill。

## 7.2 Tool 元数据建议

每个 Tool 应至少有：

- `name`
- `description`
- `input_schema`
- `mode`
- `executor`

建议 `mode`：

- `sync`
- `async`
- `task_spawn`

## 7.3 ToolRegistry 职责

- 注册工具
- 对模型暴露工具清单
- 校验参数
- 执行工具
- 返回结构化结果

## 8. Skill 调度设计

## 8.1 Skill 调度边界

`agent-core` 不直接操作硬件，而是通过 Skill 间接发起能力调用。

例如：

- 拍照由 `camera_capture_skill` 完成
- 播放音频由 `audio_play_skill` 完成
- 查询任务状态由 `task_manage_skill` 完成

## 8.2 Skill 调用结果类型

建议统一为三类：

- `direct_result`
- `device_command_issued`
- `task_spawned`

## 8.3 示例

用户说“帮我看一下前面有什么”：

1. Agent 识别需要视觉输入
2. 调用 `camera_capture_skill`
3. Skill 向眼镜发命令
4. 图片回传后，Skill 或 Agent 再调用模型理解
5. Agent 生成回复

## 9. 与后台任务中心协作

## 9.1 何时创建后台任务

满足以下任一条件时建议创建后台任务：

- 任务持续时间较长
- 任务需要状态跟踪
- 任务需要异步通知
- 任务需要跨端协同

示例：

- 计时器
- 导航
- 手机视频流任务
- 找物 / 找通路

## 9.2 TaskGateway 职责

- 向 `backend-task-core` 提交 `task.create`
- 查询任务状态
- 请求取消任务
- 将任务摘要反馈给 Agent

## 10. 响应规划设计

模型输出不能直接等于设备输出，需要经过 `ResponsePlanner`。

职责：

- 将模型自然语言结果转换为设备可执行回复
- 判断是否需要播报
- 判断是否需要插播
- 判断是否需要转成 `audio.play` 或其他命令

## 11. 建议类与接口

- `AgentRuntime`
- `AgentInput`
- `AgentOutput`
- `ConversationContextStore`
- `ModelAdapter`
- `ToolRegistry`
- `ToolSpec`
- `ToolExecutor`
- `SkillGateway`
- `TaskGateway`
- `ResponsePlanner`

## 12. 第一阶段必须实现的最小能力

- 读取对话上下文
- 调用百炼模型
- 注册并调用至少 2 个 Tool / Skill
- 向后台任务中心创建任务
- 将最终回复转为眼镜播报命令

## 13. 高风险点

- 模型工具调用输出不稳定
- 实时语音场景下上下文切换复杂
- 工具结果体积过大导致上下文污染

应对策略：

- 使用结构化工具定义
- 对工具结果统一做摘要
- Tool 执行与设备控制之间增加一层 Planner

## 14. 与后续文档关系

本文是以下文档前置：

- 《拍照Skill设计》
- 《后台任务运行时设计》
- 《非实时语音对话方案设计》
- 《实时语音对话与打断方案设计》

## 15. 当前建议的下一步

建议继续输出：

1. 《后台任务运行时设计》
2. 《Skill与MCP接入设计》

原因：

- Agent 是开放式中枢。
- 下一步要补足它依赖的 Task 和 Skill/MCP 两个基础框架。
