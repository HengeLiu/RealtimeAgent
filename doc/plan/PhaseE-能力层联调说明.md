# Phase E 能力层联调说明

## 1. 目标

本说明用于验证 Phase E 已完成以下能力层落地：

1. `ToolRegistry / ToolGateway`
2. `SkillRegistry / SkillGateway`
3. `McpRegistry / McpGateway`
4. `capture_photo / timer_manage / amap_route_plan` 的最小闭环

联调重点观察：

1. `CapabilityTrace` 是否会记录 `tool / skill / mcp / task`
2. 能力调用产生的 `asset_refs / derived_artifacts / task_refs` 是否会写回会话
3. AMap 是否能先以 mock 方式稳定返回结构化结果

## 2. 自动化验证

当前阶段主测试脚本：

```bash
bash script/run_tests.sh
```

若只验证 Phase E 相关能力层：

```bash
PYTHONPATH=server/src python -m unittest \
  server.test.unit.test_agent_core \
  server.test.integration.test_agent_phase_e_flow -v
```

预期结果：

1. `unit.test_agent_core.*` 全部通过
2. `integration.test_agent_phase_e_flow.*` 通过
3. 输出中能看到 `photo_interpret`、`timer_manage`、`amap_route_plan` 相关断言全部通过

## 3. 服务端启动

在仓库根目录执行：

```bash
export DASHSCOPE_API_KEY="<your-api-key>"
export DEVICE_TOKEN_MAP="glass-001=pair-demo-token"
PYTHONPATH=server/src python -m app.main --host 0.0.0.0 --port 8765
```

说明：

1. 当前 `OpenAIAgentLoopRunner` 已补了若干直连能力路由，便于在没有真实模型工具决策的情况下观察 Phase E 能力层行为。
2. AMap 当前默认走 mock adapter，不依赖真实第三方配置。

## 4. 建议联调话术

建议依次说以下话：

1. `帮我看看前面有什么`
2. `帮我定时 5 分钟`
3. `导航去最近的咖啡店`

预期行为：

1. 第一句会命中 `photo_interpret`，内部触发 `capture_photo`
2. 第二句会命中 `timer_manage`，内部触发 `create_timer`
3. 第三句会命中 `amap_route_plan`，并通过 mock AMap 返回路线摘要

## 5. 联调观察点

### 5.1 服务端日志

重点关注以下日志模式：

1. `Agent 输出: action=... traces=[...]`
2. `capability_name='capture_photo'`
3. `capability_name='photo_interpret'`
4. `capability_name='timer_manage'`
5. `capability_name='amap_route_plan'`

说明：

1. 若只看到最终回复，看不到 trace，说明能力调用没有进入统一网关。
2. 若 `photo_interpret` 成功但助手消息没有图片资产，说明 `AgentTurnResult.meta -> AgentFacade` 的结果回写链路有问题。

### 5.2 会话上下文

可以在联调代码里直接读取 `AgentSessionStore`，重点检查：

1. `session.assets`
2. `session.artifacts`
3. `session.tasks`
4. `session.capability_traces`
5. `session.messages[assistant].asset_refs/derived_refs/task_refs`

预期：

1. 图片解读后至少新增 1 个 `image` 资产和 1 个 `image_interpretation` 派生结果
2. 计时器创建后至少新增 1 个 `TaskRef`
3. 导航后至少新增 1 个 `amap_route_plan` 派生结果

## 6. AMap mock / 真实环境切换说明

当前实现默认：

1. `AmapMcpAdapter(mock_mode=True)`
2. 不访问真实第三方环境

如果后续要切到真实环境，建议按以下步骤做：

1. 在 `AmapMcpAdapter` 内补真实 provider 调用实现
2. 为真实 provider 单独增加配置项，不要复用语音模型配置
3. 保留 `mock_mode`，让自动化测试继续使用稳定 stub
4. 在 Phase H 文档中单独维护真实 AMap 配置和排障说明

## 7. 当前限制

1. `capture_photo` 当前是模拟抓拍，不代表真实相机链路已打通。
2. `timer_manage` 当前通过 `InMemoryTaskGateway` 工作，不代表完整后台任务状态机已完成。
3. `amap_route_plan` 当前是 mock 结果，不代表真实路线质量。
4. 若要验证完整语音主链路，仍需在允许本地绑定端口的环境中执行 socket 级集成测试。
