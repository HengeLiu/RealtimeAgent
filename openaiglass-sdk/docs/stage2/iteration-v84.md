# sdk-v84 外部 MCP、Task 调度与通知链路修复

## 背景

业务侧反馈三类 SDK 边界问题会影响真实业务闭环：

1. 业务只能注册 `BaseMcpAdapter`，不能直接配置并连接官方 MCP Server 的 stdio/SSE 进程。
2. SDK 自定义 Task 没有公开通用定时调度接口，也缺少终态事件“先回流 Agent 决策，再通知用户”的声明字段。
3. `DeviceGroupContext.submit_notification(...)` 只进入 `DeviceGroupRuntime` 通知记录，真实 `ControlRuntime` 没有把通知适配器绑定到 `VoiceRuntime` 播报入口。

## 变更

1. 新增 `ExternalMcpServerConfig` 和 `ExternalMcpAdapter`：
   - 支持 `stdio`、`sse`、`streamable_http` 三种外部 MCP Server 连接方式。
   - 通过官方 MCP Python SDK 读取 tools/list，并映射成 SDK `McpMethodSpec`。
   - 新增 `OpenAIGlassesSDK.register_external_mcp_server(...)`，业务宿主可用配置注册官方 AMap MCP Server。
   - 新增可选依赖 `openaiglasses-sdk[mcp]`，避免普通 SDK 安装强制拉取 MCP client 依赖。
2. 新增 SDK 自定义 Task 调度能力：
   - `TaskContext.schedule_event(...)` 可安排一次性延迟事件。
   - `DeviceGroupContext.schedule_task_event(...)` 可从设备组上下文安排目标任务事件。
   - `TaskRuntimeManager` 负责定时器、幂等事件编号、终态保护和调度事件日志。
3. 新增 Task 终态事件策略字段：
   - `terminal_event_requires_agent_decision`
   - `terminal_event_allow_direct_notify`
   - `terminal_event_priority`
   - 调度器触发的终态事件会按这些字段发布给后台任务事件监听器。
4. 修复设备组通知真实播报链路：
   - `ControlRuntime` 初始化时绑定 `DeviceGroupRuntime.notification_adapter`。
   - `VoiceRuntime.submit_notification(...)` 统一把外部通知送入 `NotificationCoordinator` 和播放仲裁链路。
   - `context.submit_notification(...)` 现在会触发真实 `assistant.reply` / `actuator.audio.play`，不再只增加 `notification_count`。

## 开发者使用方式

外部 MCP Server：

```python
sdk.register_external_mcp_server(
    ExternalMcpServerConfig(
        name="amap",
        transport="stdio",
        command="npx",
        args=["-y", "@amap/amap-maps-mcp-server"],
        env={"AMAP_MAPS_API_KEY": "..."},
        method_prefix="amap",
    )
)
```

Task 定时调度：

```python
class TimerTask(BaseTask):
    task_type = "timer_task"
    terminal_event_requires_agent_decision = True
    terminal_event_allow_direct_notify = False

    def on_start(self, context):
        context.emit_state("running")
        context.schedule_event(delay_ms=3000, event_name="timer.fired")

    def on_event(self, context, event):
        if event.name == "timer.fired":
            context.complete({"message": "计时结束"})
```

## 验证

已执行：

```bash
uv run python -m py_compile \
  openaiglass-sdk/server-python/agent_core/mcp/external_client.py \
  openaiglass-sdk/server-python/openaiglasses/capabilities/base_task.py \
  openaiglass-sdk/server-python/openaiglasses/runtime/tasks.py \
  openaiglass-sdk/server-python/openaiglasses/runtime/device_group.py \
  openaiglass-sdk/server-python/openaiglasses/server.py \
  openaiglass-sdk/server-python/runtime/voice_runtime.py \
  openaiglass-sdk/server-python/api/ws/control_runtime.py

uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_sdk_phase_two.py \
  openaiglass-sdk/tests/unit/test_task_event_runtime.py \
  -q
```

结果：

1. 静态编译通过。
2. 相关单元测试 50 条通过。

## 设备级回放状态

本轮未执行完整 `glass-playback` 设备级回放。原因是改动集中在 SDK 公开扩展面、任务运行时和通知适配绑定，已用单元测试覆盖外部 MCP 映射、调度事件、终态策略和通知协调入口。下一轮业务侧可用 timer 场景通过 `glass-playback` 验证 `assistant.reply` / `actuator.audio.play` 是否出现在事件日志中。
