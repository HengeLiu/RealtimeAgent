# backend-task-core 设计

## 0. 术语说明

本文中的 `backend-task-core` 即系统中的 `task-core`，表示与 `agent-core` 同等级、同分量的独立后台任务运行模块。

## 1. 文档定位

本文档是当前项目 `backend-task-core` 的详细设计文档。

本文档重点解决以下问题：

1. `backend-task-core` 与 `agent-core` 的职责边界是什么。
2. 两者之间通过什么对象、接口和事件结合。
3. `backend-task-core` 内部需要哪些核心模块。
4. 任务实例、状态机、事件流、调度器、上下文存储如何设计。
5. 第一阶段为什么应先落 `timer_task`，以及后续如何平滑扩展到 `navigation_task`、`phone_video_link_task`。

本文档是 [agent-core设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/agent-core设计.md) 中 `task runtime` 部分的详细展开稿。

---

## 2. 设计目标

`backend-task-core` 的设计目标如下：

1. 统一承接所有长生命周期后台任务。
2. 让 `agent-core` 不必维护长期任务运行状态。
3. 让不同任务模板都能在统一框架下注册、运行和观测。
4. 让任务事件能够标准化回流 `agent-core`，形成新的对话输入。
5. 让后续开发者新增任务模板时，只需要实现 `Task` 模板和状态迁移规则，不需要修改核心运行时。

---

## 3. 设计原则

1. `agent-core` 与 `backend-task-core` 是平级独立模块，前者偏开放式决策，后者偏后台执行。
2. 任务状态必须结构化保存，不能只放在临时内存变量里。
3. 任务运行必须有统一生命周期和统一事件模型。
4. 任务模板之间共享一套运行时框架，但不共享业务状态。
5. 任务完成、失败、超时等结果必须先产出标准事件，再根据事件优先级决定是否需要回流 `agent-core` 或直接通知设备。
6. 第一阶段优先做“正确的边界”和“最小可用骨架”，而不是提前追求过强的泛化能力。

---

## 4. 核心结论

### 4.1 `agent-core` 与 `backend-task-core` 的最终边界

#### `agent-core` 负责

1. 理解用户意图。
2. 判断是否需要创建后台任务。
3. 判断需要创建哪一种任务。
4. 判断需要查询、取消、暂停还是恢复任务。
5. 在收到任务事件后决定是否播报、追问、确认或继续调用其他能力。
6. 在对话执行链路中直接调用 Tool、Skill、MCP 等能力。

#### `backend-task-core` 负责

1. 创建任务实例。
2. 保存任务上下文。
3. 驱动任务状态机。
4. 管理任务生命周期。
5. 发布任务事件。
6. 接受任务控制命令。
7. 对接设备层、MCP、手机侧任务中心或其他执行资源。
8. 在后台任务执行过程中直接调用 Tool、Skill、MCP 和设备能力。

### 4.2 关键约束

1. `agent-core` 不能直接持有任务实例内部状态。
2. `backend-task-core` 不能直接做自然语言决策。
3. `backend-task-core` 发布的是结构化事件，不是面向用户的最终回复文案。
4. 所有任务都必须注册为标准 `Task` 模板，不能以临时线程或临时协程偷偷运行。

---

## 5. 整体架构

建议 `backend-task-core` 由以下模块组成：

1. `TaskRegistry`
2. `TaskManager`
3. `TaskContextStore`
4. `TaskScheduler`
5. `TaskEventBus`
6. `TaskStateMachine`
7. `TaskExecutor`
8. `TaskGateway`

### 5.1 模块职责

#### `TaskRegistry`

职责：

1. 注册 `Task` 模板。
2. 根据 `task_type` 返回任务模板定义。

#### `TaskManager`

职责：

1. 任务创建入口。
2. 任务查询入口。
3. 任务取消、暂停、恢复入口。
4. 统一管理内存中的活动任务句柄。

#### `TaskContextStore`

职责：

1. 保存任务实例对象。
2. 保存任务输入、状态、结果、错误、事件索引。
3. 保存任务与 `session_id`、`device_id`、`parent_task_id` 等关系。

#### `TaskScheduler`

职责：

1. 负责延迟触发。
2. 负责超时监控。
3. 负责定时任务唤醒。

#### `TaskEventBus`

职责：

1. 发布任务生命周期事件。
2. 提供订阅能力。
3. 把任务事件转给 `agent-core` 或其他运行时。

#### `TaskStateMachine`

职责：

1. 定义统一状态。
2. 校验状态迁移是否合法。
3. 避免任务模板各自发明不同状态语义。

#### `TaskExecutor`

职责：

1. 执行任务模板的业务逻辑。
2. 执行状态迁移。
3. 产出领域事件。

#### `TaskGateway`

职责：

1. 为 `agent-core`、`tool`、`skill`、`task` 提供稳定调用接口。
2. 隐藏 `backend-task-core` 内部实现细节。

---

## 6. 任务对象模型

建议至少定义以下核心对象。

## 6.1 TaskSpec

表示一个 `Task` 模板定义，是开发者扩展后台能力的核心注册对象。

建议字段：

```json
{
  "task_type": "timer_task",
  "version": "v1",
  "description": "计时器后台任务",
  "input_schema": {
    "duration_seconds": "int"
  },
  "supports_cancel": true,
  "supports_pause": false,
  "supports_resume": false,
  "timeout_seconds": 86400
}
```

字段说明：

1. `task_type`
   - 任务类型唯一标识。
2. `version`
   - 模板版本。
3. `description`
   - 任务说明。
4. `input_schema`
   - 输入参数结构。
5. `supports_cancel / pause / resume`
   - 能力声明。
6. `timeout_seconds`
   - 默认超时配置。

## 6.2 TaskRuntime

表示某个 `Task` 被启动后的任务实例。

建议字段：

```json
{
  "task_id": "task_01J...",
  "task_type": "timer_task",
  "version": "v1",
  "session_id": "sess_01J...",
  "device_id": "glass-001",
  "owner_role": "agent",
  "state": "running",
  "input": {
    "duration_seconds": 180
  },
  "context": {},
  "result": null,
  "error": null,
  "created_at": 1744262400000,
  "updated_at": 1744262405000,
  "started_at": 1744262401000,
  "completed_at": null,
  "parent_task_id": null
}
```

字段说明：

1. `session_id`
   - 关联会话。
2. `device_id`
   - 关联设备。
3. `owner_role`
   - 当前默认为 `agent`。
4. `input`
   - 任务初始输入。
5. `context`
   - 任务内部运行上下文。
6. `result`
   - 完成时结果。
7. `error`
   - 失败时错误。

## 6.3 Task

`Task` 是未启动的后台任务模板代码，是与 `Tool / Skill / MCP` 同等级的一等扩展单元。

其本质是：

1. 一套可注册、可发现的后台能力模板。
2. 一段在 `backend-task-core` 中运行的任务逻辑。
3. 一个允许自由组装 Tool、Skill、MCP、设备网关和子任务的执行单元。

建议每个 `Task` 至少声明：

1. `TaskSpec`
2. 输入校验逻辑
3. 状态迁移规则
4. 运行入口
5. 事件产出规则
6. 可调用的底层能力范围

## 6.4 TaskCommand

表示对任务的控制命令。

建议类型：

1. `create`
2. `query`
3. `cancel`
4. `pause`
5. `resume`

示例：

```json
{
  "command_id": "cmd_01J...",
  "task_id": "task_01J...",
  "command_type": "cancel",
  "issued_by": "agent-core",
  "session_id": "sess_01J..."
}
```

## 6.5 TaskEvent

表示任务生命周期事件。

建议字段：

```json
{
  "event_id": "evt_01J...",
  "event_name": "task.completed",
  "task_id": "task_01J...",
  "task_type": "timer_task",
  "session_id": "sess_01J...",
  "device_id": "glass-001",
  "priority": "normal",
  "ts": 1744262580000,
  "payload": {
    "message": "三分钟计时已结束"
  }
}
```

字段补充说明：

1. `priority`
   - 表示事件优先级。
   - 建议统一为 `low / normal / high / critical`。
   - 用于决定该事件是否允许绕过 `agent-core` 直接通知设备。

---

## 7. 统一状态机设计

## 7.1 基础状态

所有任务至少支持以下统一状态：

1. `created`
2. `scheduled`
3. `running`
4. `waiting_external`
5. `completed`
6. `cancelled`
7. `failed`
8. `timeout`

### 状态说明

#### `created`

任务实例已创建，但尚未进入调度器或执行器。

#### `scheduled`

任务已进入调度队列，等待未来某个时点启动或唤醒。

#### `running`

任务当前处于主动执行态。

#### `waiting_external`

任务在等待外部事件，例如：

1. 手机侧回传结果
2. 眼镜抓拍完成
3. 用户确认

#### `completed`

任务正常完成。

#### `cancelled`

任务被显式取消。

#### `failed`

任务异常终止。

#### `timeout`

任务超时终止。

## 7.2 基础状态迁移

统一允许：

1. `created -> scheduled`
2. `created -> running`
3. `scheduled -> running`
4. `running -> waiting_external`
5. `waiting_external -> running`
6. `running -> completed`
7. `waiting_external -> completed`
8. `running -> cancelled`
9. `waiting_external -> cancelled`
10. `running -> failed`
11. `waiting_external -> failed`
12. `scheduled -> cancelled`
13. `running -> timeout`
14. `waiting_external -> timeout`

统一禁止：

1. `completed` 再回到 `running`
2. `cancelled` 再回到 `running`
3. `failed` 再回到 `running`
4. `timeout` 再回到 `running`

## 7.3 为什么必须统一状态机

原因：

1. 便于任务查询统一展示。
2. 便于 `agent-core` 理解任务状态。
3. 避免不同任务模板各自定义不兼容状态名。

---

## 8. 生命周期事件设计

## 8.1 标准事件名

建议统一标准事件名：

1. `task.created`
2. `task.scheduled`
3. `task.started`
4. `task.state.changed`
5. `task.progress.updated`
6. `task.waiting_external`
7. `task.completed`
8. `task.cancelled`
9. `task.failed`
10. `task.timeout`

## 8.2 事件使用原则

1. `task.state.changed` 用于通用状态同步。
2. `task.completed / failed / cancelled / timeout` 用于强语义终态。
3. `task.progress.updated` 用于长任务增量汇报。
4. `task.waiting_external` 用于表达任务当前被外部依赖阻塞。

## 8.3 事件与用户回复的关系

必须遵守：

1. 事件始终是结构化系统信号。
2. 是否直接面向用户下发通知，必须受统一通知策略控制。

推荐策略：

1. `low`
   - 只回流 `agent-core`，不直接通知设备。
2. `normal`
   - 默认回流 `agent-core`，由 `agent-core` 决定是否播报。
3. `high`
   - 允许直接通知设备，同时回流 `agent-core` 做上下文同步。
4. `critical`
   - 应优先直接通知设备，并同步回流 `agent-core`。

---

## 9. `agent-core` 与 `backend-task-core` 结合方式

## 9.1 创建任务

推荐流程：

1. 用户输入进入 `agent-core`
2. Agent 判断需要创建任务
3. Agent 调用对应 Tool
4. Tool 调用 `TaskGateway.create_task(...)`
5. `TaskManager` 创建实例
6. `TaskManager` 发布 `task.created`
7. Tool 返回 `task_id`
8. Agent 生成确认回复

### 约束

1. Agent 不直接 new 一个 TaskRuntime。
2. Tool 不直接维护任务线程。
3. 创建入口可以来自 `agent-core`，也可以来自另一个正在运行的 `Task`。

## 9.2 查询任务

推荐流程：

1. 用户问“还有多久”
2. Agent 调用 `query_task_status`
3. Tool 调用 `TaskGateway.query_task(...)`
4. `TaskContextStore` 返回标准任务快照
5. Tool 返回结构化结果
6. Agent 生成自然语言答复

## 9.3 取消任务

推荐流程：

1. 用户说“取消计时”
2. Agent 调用 `cancel_task`
3. Tool 调用 `TaskGateway.cancel_task(...)`
4. `TaskManager` 发起状态迁移
5. 发布 `task.cancelled`
6. Agent 生成确认回复

## 9.4 任务事件回流

推荐流程：

1. `TaskEventBus` 发布 `task.completed`
2. 通知策略先根据 `priority` 判断是否需要直接通知设备
3. 事件桥接器将事件转换成新的 `AgentTurn`
4. `agent-core` 读取当前会话上下文
5. Agent 决定是否：
   - 直接播报
   - 等待当前回复结束再播报
   - 合并到其他任务结果里播报
6. `voice-runtime` 完成播报

## 9.5 为什么任务结果通常需要回流 AgentTurn

原因：

1. 统一对话中心。
2. 允许结合上下文做不同回复。
3. 为未来多任务、优先级、打断策略留空间。
4. 即使某些高优先级事件允许直接通知设备，也仍需要保留对话侧上下文同步。

---

## 10. 与设备层、MCP、手机侧的关系

## 10.1 与设备层

`backend-task-core` 可以通过 `TaskGateway` 内部依赖访问：

1. `DeviceGateway`
2. `CameraGateway`
3. `AudioGateway`
4. `ToolGateway`
5. `SkillGateway`
6. `McpGateway`

但约束是：

1. 任务模板不能直接操作底层 WebSocket。
2. 一切设备控制必须走统一网关。

## 10.2 与 MCP

任务模板可以调用 MCP，但必须通过统一 MCP Gateway。

原因：

1. 统一鉴权。
2. 统一超时。
3. 统一错误模型。

## 10.3 与手机侧

后续手机加入后，`backend-task-core` 需要支持“跨端任务”。

例如：

1. `phone_video_link_task`
2. `navigation_task`

这类任务的共同特征：

1. 服务器负责任务控制平面。
2. 眼镜和手机负责数据平面。
3. 任务实例仍由服务器 `backend-task-core` 统一托管。

---

## 11. Task 扩展设计

## 11.1 设计目标

`Task` 是 `backend-task-core` 中最重要的扩展单元。

设计目标：

1. 让其他开发者可以像扩展 Tool、Skill、MCP 一样扩展后台任务能力。
2. 让每个 `Task` 都在统一生命周期、统一状态机、统一事件模型下运行。
3. 让 `Task` 可以在内部自由组装所有原子能力，而不需要修改运行时框架。

## 11.2 Task 与其他能力的关系

1. `Tool`
   - 面向同步或短生命周期原子动作。
2. `Skill`
   - 面向短期内的复合业务能力。
3. `MCP`
   - 面向远程工具服务。
4. `Task`
   - 面向长生命周期后台能力模板。

核心差异在于：

1. `Task` 运行在后台。
2. `Task` 有自己的状态机和事件流。
3. `Task` 可以在内部调用 Tool、Skill、MCP，甚至创建子任务。

## 11.3 Task 开发者扩展约束

新增一个 `Task` 时，开发者至少需要提供：

1. `TaskSpec`
2. 输入参数定义
3. 状态迁移规则
4. `run()` 或等价执行入口
5. 事件产出规则
6. 错误处理策略

运行时只负责：

1. 注册
2. 调度
3. 实例托管
4. 状态迁移校验
5. 事件分发

---

## 12. 第一阶段任务模板设计

## 12.1 `timer_task`

定位：

1. 第一个最小后台任务模板。

输入：

1. `duration_seconds`

流程：

1. 创建后进入 `scheduled` 或 `running`
2. 注册延时触发
3. 到点后进入 `completed`
4. 发布 `task.completed`

特点：

1. 无外部设备依赖
2. 无复杂状态迁移
3. 非常适合先验证骨架

## 12.2 `navigation_task`

定位：

1. 导航类长期任务模板。

输入：

1. 目的地
2. 出行方式
3. 路线偏好

典型状态：

1. `created`
2. `waiting_external`
3. `running`
4. `completed`
5. `failed`

特点：

1. 依赖地图能力
2. 依赖设备状态
3. 可能依赖手机侧持续能力

### 当前结论

第一阶段不实现完整 `navigation_task`，但设计必须为它预留：

1. `progress.updated`
2. `waiting_external`
3. 跨端执行

## 12.3 `phone_video_link_task`

定位：

1. 眼镜与手机建立直连视频链路的长期任务。

特点：

1. 明显跨设备
2. 有准备态、运行态、异常态、结束态
3. 对任务状态机要求高

### 当前结论

只作为第二阶段设计目标预留。

---

## 13. 失败处理与恢复策略

## 13.1 任务失败

统一要求：

1. 写入 `error`
2. 状态迁移到 `failed`
3. 发布 `task.failed`

## 13.2 任务超时

统一要求：

1. 由 `TaskScheduler` 负责检测
2. 迁移到 `timeout`
3. 发布 `task.timeout`

## 13.3 服务重启恢复

第一阶段可暂不实现完整恢复。

但必须预留：

1. `TaskContextStore` 持久化接口
2. `TaskManager.restore_active_tasks()` 启动入口

这样后续可以平滑加上恢复能力。

---

## 14. 接口设计建议

建议统一暴露以下内部接口：

### 14.1 TaskGateway

```python
create_task(task_type, session_id, device_id, input) -> TaskRuntime
query_task(task_id) -> TaskRuntime
list_tasks(session_id=None, device_id=None, state=None) -> list[TaskRuntime]
cancel_task(task_id) -> TaskRuntime
pause_task(task_id) -> TaskRuntime
resume_task(task_id) -> TaskRuntime
```

### 14.2 Event Bridge

```python
publish_task_event(event: TaskEvent) -> None
subscribe_task_events(handler) -> None
should_direct_notify(event: TaskEvent) -> bool
convert_task_event_to_agent_turn(event: TaskEvent) -> AgentTurn
```

---

## 15. 实施优先级

建议实施顺序如下：

1. 先落 `TaskStateMachine`
2. 再落 `TaskRuntime / TaskSpec / TaskEvent`
3. 再落 `TaskRegistry + TaskContextStore`
4. 再落 `TaskManager + TaskGateway`
5. 再落 `TaskEventBus`
6. 再落 `TaskScheduler`
7. 最后实现 `timer_task`

原因：

1. 先把边界和对象模型固定下来。
2. 再把调度和业务模板接进去。

---

## 16. 与 `agent-core设计.md` 的关系

本设计稿是 `backend-task-core` 的详细展开文档。

最终约束如下：

1. `agent-core设计.md` 保留系统级最终边界。
2. 本文档负责详细定义 `backend-task-core` 的内部设计。
3. 后续具体实现时，凡是涉及：
   - 任务模板
   - 状态机
   - 调度器
   - 事件总线
   - 任务桥接
   都应优先参考本文档。

---

## 17. 最终设计结论

1. `backend-task-core` 必须作为独立运行时中心存在。
2. `agent-core` 与 `backend-task-core` 的边界应保持“开放式决策”和“后台执行”分工，但两者都允许直接调用各种工具能力。
3. `backend-task-core` 的核心不是某个任务模板，而是统一的 `Task` 扩展模型、任务实例模型、状态机、事件流和调度机制。
4. 第一阶段应先做 `timer_task` 验证运行时骨架，再扩展到导航和跨设备任务。
5. 所有任务结果都必须先产出结构化事件；是否允许绕过 `agent-core` 直接通知用户，取决于事件优先级和统一通知策略。
