# Phase E 能力层联调说明

## 1. 目标

本说明用于验证 Phase E 已完成以下能力层落地：

1. `ToolRegistry / ToolGateway`
2. `McpRegistry / McpGateway`
3. `capture_photo / timer_manage / map_manage` 的最小闭环

联调重点观察：

1. `CapabilityTrace` 是否会记录 `tool / mcp / task`
2. 能力调用产生的 `asset_refs / derived_artifacts / task_refs` 是否会写回会话
3. AMap 是否能先以 mock 方式稳定返回结构化结果

## 2. 自动化验证

当前阶段主测试脚本：

```bash
bash script/run_tests.sh
```

若只验证 Phase E 相关能力层：

```bash
PYTHONPATH=openaiglass-sdk/server-python python -m unittest \
  server.test.unit.test_agent_core \
  server.test.integration.test_agent_phase_e_flow -v
```

预期结果：

1. `unit.test_agent_core.*` 全部通过
2. `integration.test_agent_phase_e_flow.*` 通过
3. 输出中能看到 `capture_photo`、`timer_manage`、`map_manage` 相关断言全部通过

## 3. 服务端启动

在仓库根目录执行：

```bash
export DASHSCOPE_API_KEY="<your-api-key>"
export DEVICE_TOKEN_MAP="glass-001=pair-demo-token"
export LOG_FILE="logs/server.log"
export AGENT_MODEL_NAME="qwen3.6-plus"
export VOICE_MODEL_NAME="qwen3.5-omni-plus"
PYTHONPATH=openaiglass-sdk/server-python python -m app.main --host 0.0.0.0 --port 8765
```

说明：

1. `AGENT_MODEL_NAME` 用于 agent-core 文本决策与图片理解，建议当前使用 `qwen3.6-plus`。
2. `TTS_MODEL_NAME` 默认使用 `cosyvoice-v3-flash`；若依赖缺失则自动回退旧 TTS 链路。
3. 当前 `OpenAIAgentLoopRunner` 已回到标准 SDK tool calling 主路径，不再保留图片、计时器、导航和设备状态的直连能力路由。
4. AMap 当前默认走 mock adapter，不依赖真实第三方配置。
5. 若设置了 `LOG_FILE`，服务端会在标准输出之外，额外把同样的 JSON 结构化日志写入该文件，便于长期保留 `tool.call/result`、`mcp.call/result` 调试链路。
6. 当前发给模型的历史上下文已直接采用 `history messages`，不再把历史、资产和派生结果压成一整段说明文本。
7. 当前模型侧只暴露 3 个高层工具：`capture_photo / timer_manage / map_manage`。
8. 图片理解改由主链路模型直接接收文本与图片完成；`capture_photo` 只负责取图。

## 4. 建议联调话术

建议依次说以下话：

1. `帮我看看前面有什么`
2. `帮我定时 5 分钟`
3. `导航去最近的咖啡店`

预期行为：

1. 第一句会命中 `capture_photo`，随后主链路模型继续查看真实图片
2. 第二句会命中 `timer_manage`，内部触发 `create_timer`
3. 第三句会命中 `map_manage`，内部再调用 mock AMap 返回路线摘要

## 5. 联调观察点

### 5.1 服务端日志

重点关注以下日志模式：

1. `Agent 输出: has_error=... traces=[...]`
2. `capability_name='capture_photo'`
3. `拍照后切换到主链路图片解读`
4. `主链路图片解读完成`
5. `capability_name='timer_manage'`
6. `capability_name='amap.route_plan'`
7. `capability_name='map_manage'`
8. `CosyVoice 流式 TTS 初始化成功`

说明：

1. 若只看到最终回复，看不到 trace，说明能力调用没有进入统一网关。
2. 若拍照后第二次模型请求里仍只有 `asset_id / storage_uri` 这样的文本，而没有真正的 `image_url`，说明还没有切到新的主链路图片解读实现。
3. 若日志出现 `CosyVoice 流式 TTS 初始化失败，回退全文 TTS`，说明当前环境仍在走旧的全文 TTS 降级路径。
4. 若抓拍回传图片较大，`sensor.camera.captured` 可能被拆成多个 WebSocket 分片；当前服务端已支持重组分片后再做 JSON 解码，若仍看到 `ControlMessage JSON 解码失败`，需要继续排查设备端是否发送了损坏文本。

### 5.2 会话上下文

可以在联调代码里直接读取 `AgentSessionStore`，重点检查：

1. `session.assets`
2. `session.artifacts`
3. `session.tasks`
4. `session.capability_traces`
5. `session.messages[assistant].asset_refs/derived_refs/task_refs`

预期：

1. 图片解读后至少新增 1 个 `image` 资产
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

1. 本文编写时 `capture_photo` 仍是模拟抓拍；真实相机链路现已补到 [PhaseG-真实抓拍图片联调说明.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage1/develop/PhaseG-真实抓拍图片联调说明.md)。
2. `timer_manage` 当前通过 `InMemoryTaskGateway` 工作，不代表完整后台任务状态机已完成。
3. `map_manage` 当前内部调用的 AMap 结果仍是 mock，不代表真实路线质量。
4. 若要验证完整语音主链路，仍需在允许本地绑定端口的环境中执行 socket 级集成测试。
