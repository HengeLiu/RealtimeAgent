# Skill与MCP接入设计

## 1. 文档目标

本文定义系统内部 Skill 与外部 MCP 能力的统一接入方式、注册机制、执行模式和边界关系。

## 2. 设计目标

- 给 Agent 和 Task 提供统一能力入口
- 屏蔽底层实现差异
- 将内部能力与外部能力结构化暴露
- 保证新增能力可以低成本接入

## 3. Skill 与 MCP 的关系

### 3.1 Skill

定位：

- 系统内部原子能力

特点：

- 接口统一
- 可直接调用设备能力或内部服务
- 更贴近业务动作

### 3.2 MCP

定位：

- 外部服务或平台能力适配层

特点：

- 屏蔽外部 API 差异
- 输出结构化结果

### 3.3 关系建议

第一阶段建议：

- MCP 保持为独立层
- 允许在需要时用 Skill 封装 MCP

例如：

- `amap_route_skill` 内部调用 `amap_adapter`

## 4. Skill 设计

## 4.1 Skill 最小接口

每个 Skill 应至少定义：

- `name`
- `description`
- `input_schema`
- `output_schema`
- `mode`
- `execute()`

## 4.2 Skill 模式

- `sync`
- `async`
- `task_spawn`

## 4.3 Skill 输入输出原则

- 输入参数必须结构化
- 输出必须结构化
- 错误必须统一为 `Error` 模型

## 4.4 Skill 注册机制

建议：

- 启动时集中注册
- 使用 `SkillRegistry`
- 每个 Skill 名称全局唯一

## 4.5 第一阶段建议内置 Skill

- `camera_capture_skill`
- `audio_play_skill`
- `task_manage_skill`
- `phone_video_link_skill`
- `device_state_query_skill`

## 5. MCP 设计

## 5.1 MCP 最小接口

每个 MCP 适配器应至少定义：

- `name`
- `description`
- `request_schema`
- `response_schema`
- `invoke()`

## 5.2 MCP 注册机制

建议：

- 使用 `McpRegistry`
- 按名称注册
- 对鉴权和配置做独立管理

## 5.3 第一阶段建议接入 MCP

- `amap_adapter`

## 6. Agent / Task 如何使用 Skill 与 MCP

### 6.1 Agent 使用

- Agent 通过 `ToolRegistry` 间接调用 Skill 或 MCP
- 不直接依赖具体实现类

### 6.2 Task 使用

- Task 直接注入 `SkillGateway`
- 当需要访问外部服务时，可走 MCP 或被 Skill 封装后的能力

## 7. 执行结果模型

建议结果统一包含：

- `status`
- `data`
- `summary`
- `task_id`
- `error`

其中：

- `status` 可取 `completed` / `failed` / `accepted`

## 8. 权限与安全边界

第一阶段建议至少控制：

- 哪些 Skill 可由 Agent 直接调用
- 哪些 Skill 只能由 Task 调用
- 哪些 MCP 需要敏感配置

## 9. 测试建议

- Skill 参数校验测试
- Skill 注册表测试
- MCP 适配器测试
- Agent 调用 Skill 集成测试

## 10. 当前建议的下一步

建议继续输出：

1. 《日志追踪与测试底座设计》
