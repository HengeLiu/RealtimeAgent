# Tool 与 Task 概念统一设计

本文面向当前新版 `realtime-agent`，论证并定义把 Task 概念并入 Tool 的目标架构。前置依赖：[ToolRun统一异步工具调用设计.md](ToolRun统一异步工具调用设计.md) 描述的 Tool Run 机制已全部落地（等待窗口、后台 runner、FollowUpRouter、待通知与唤醒注入）。

## 1. 文档定位

Tool Run 落地后，Tool 已经具备异步运行语义：调用可以在等待窗口内完成，也可以转后台、稍后把结果回流模型。这与 Task 的原始定义（后台任务、快速返回启动态、结果回流）发生了实质重合。本文回答：

1. Task 还剩哪些 Tool Run 没有覆盖的职责。
2. 这些职责如何映射到统一后的 Tool 概念上。
3. 统一后的对外模型（开发者扩展面、模型可见工具面）。
4. 哪些代码与文档将被删除。

结论先行：**Task 不再作为一等概念维护。系统只保留 Tool 与 Tool Run 两个概念**——Tool 是能力声明与实现，Tool Run 是一次调用的运行实体。原 Task 的全部职责由 ToolSpec 声明字段、Tool Run 生命周期和工具协程内联逻辑承接。

## 2. 重合性审查

### 2.1 已经重合的部分（Tool Run 落地后）

| 原 Task 职责 | 原实现 | Tool Run 对应物 |
| --- | --- | --- |
| 后台执行，不阻塞模型 turn | `TaskRunner`（独立线程事件循环） | `ToolRunRunner`（独立线程事件循环 + 独立线程池 + 每用户并发上限） |
| 启动快速返回引用 | `TaskEngine.create()` → `TaskRef(started)` | 等待窗口超窗 → `ToolResult.running(tool_run_id)` |
| 运行实体可追踪、可落盘 | `TaskRef` + `JsonlTaskStore` | `ToolRun` + `JsonlToolRunStore`（带 CAS 状态机） |
| 结果回流模型决策 | `TaskSignal(requires_agent_decision)` → `TaskSignalBridge` | `FollowUpRouter`（活跃注入 / 忙排队 / 关闭待通知 / 过期丢弃）；TaskSignal 路径已收敛至此 |
| 会话关闭后结果不丢 | 无（断头） | 待通知存储 + 唤醒注入 |
| 模型启动入口 | `TaskStartTool`（自动生成 `start_*_task`） | 普通 background Tool |

`TaskSignalBridge.requires_agent_decision` 已在 Tool Run Phase 7 转投 `FollowUpRouter`，Task 的回流通道本质上已经是 Tool Run 的回流通道。两套并行实现继续维护只剩成本，没有收益。

### 2.2 设计修正

ToolRun 设计 5.3 节当时的结论是"两者语义不同，不合并实体"，理由是 Task 是事件驱动的长生命周期 actor。审查后该结论需要修正：**actor 模型（`task.event.*` 分发到 `on_*()` hook）是"Tool 必须 3 秒内返回"这一旧约束的产物**——因为工具协程不能驻留，端侧 command 回报只能以事件分发的形式送回一个被托管的实例。等待窗口机制取消了这个前提：background Tool 的 `run()` 协程可以驻留几十秒甚至更久，完全可以在协程内用既有的 `CommandHandle.results()` 异步迭代器内联消费端侧命令回报。事件分发 actor 失去了存在的必要。

### 2.3 Task 独有、Tool Run 尚缺的能力

| 能力 | 原 Task 实现 | 统一方案 |
| --- | --- | --- |
| 端侧命令事件回流 | app 层 `command.*` → `task.event.*` → `_process_*()` actor 分发 | 工具协程内联：`handle = await context.devices.commands.start(...)`，`async for event in handle.results()` 驱动多阶段流程；设备离线由 `CommandResultBroker.fail_device_commands()` 唤醒 watcher（机制已存在） |
| 用户取消 | `task_runtime_manager.cancel` → `TaskEngine.cancel()` → `on_cancel()` | ToolRun 新增 `cancelled` 终态与 `cancel()` API；runner future 取消，工具协程在 `finally` / `except CancelledError` 中清理端侧资源；新增 `tool_run_manager` 内置工具承接 list/query/cancel |
| 定时调度（计时器到点） | `TaskScheduler.schedule_signal()` | 工具协程内 `await asyncio.sleep(seconds)`（runner 常驻循环，挂起协程成本可忽略）；重启恢复语义与现状持平（schedule 恢复本来就是未落地的目标增强） |
| 到点直通播报（不经模型） | `TaskSignal.allow_direct_notify` → `OutputService.notify_task_signal()` | `ToolSpec.late_result_notify: "model" \| "direct"`；`direct` 时 FollowUpRouter 不注入模型，改走 `OutputService` 通知仲裁直通播报（timer 类声明 direct） |
| 长命令 / 持续 stream 权限 | `TaskDeviceFacade`（`allow_long_running=True`） | `ToolContextFactory` 按 `late_result_policy=background` 注入 long-running 设备门面；`fail_fast` 工具维持短生命周期门面不变 |
| 每用户实例上限 | `TaskSpec.max_running_per_user` | `ToolSpec.max_running_per_user`（在现有同名去重之上扩展为可配 N 实例） |
| 启动回复建议 | `TaskRunResult.agent_reply` | `ToolSpec.running_message`：超窗返回 running 结果时使用工具声明的文案模板（如"计时器已开始计时"），替代统一默认文案 |
| 后台总超时强制 | `TaskScheduler.expired()` 惰性扫描 | runner 侧 `asyncio.wait_for(background_timeout)` 强制取消（当前 `deadline_at` 只记录未强制，统一时补齐）；需要按入参决定预算的工具（timer）可覆写 `background_timeout_for(input)` |

逐项核对后，没有任何 Task 能力是统一后的 Tool 表达不了的。

## 3. 统一后的对外模型

### 3.1 开发者扩展面

只有 `BaseTool` 一个基类。能力差异全部由 `ToolSpec` 声明：

```python
class FindObjectTool(BaseTool):
    spec = ToolSpec(
        name="find_object",
        description="在眼镜画面中持续寻找指定物品。",
        input_model=FindObjectInput,
        late_result_policy="background",
        background_timeout_seconds=120,
        follow_up_ttl_seconds=300,
        cancel_supported=True,
        running_message="好的，我开始找了，找到会告诉你。",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        handle = await context.devices.commands.start(
            name="peer.video.receiver.start",
            selector={"device_role": "phone"},
            params={...},
        )
        try:
            async for event in handle.results():
                if event.status == "peer.receiver.ready":
                    await context.devices.commands.start(name="peer.video.sender.start", ...)
                if event.terminal:
                    return ToolResult.success(data=event.payload, message="找到了，在沙发上。")
        finally:
            await handle.stop(reason="tool_run_finished_or_cancelled")
```

多阶段流程就是顺序协程代码，不再拆成 `run()/on_process()/on_status()/on_finish()/on_error()/on_cancel()` 六个 hook；清理逻辑就是 `finally`，不再是 `on_cancel()`。

计时器同理：

```python
class TimerTool(BaseTool):
    spec = ToolSpec(
        name="start_timer",
        description="倒计时、稍后提醒、到点提示。",
        input_model=TimerInput,
        late_result_policy="background",
        late_result_notify="direct",      # 到点直接播报，不经模型组织
        cancel_supported=True,
        running_message="计时器已开始计时。",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        await asyncio.sleep(float(input_data["seconds"]))
        return ToolResult.success(message=input_data.get("message") or "时间到了。")
```

### 3.2 模型可见工具面

1. 业务工具：`start_timer`、`find_object`、`query_route_plan` 等，统一是 Tool；不再有 `start_*_task` 命名规约。
2. `tool_run_manager`（替代 `task_runtime_manager`）：`list_instances` / `query` / `cancel`，操作 `ToolRunStore`；`cancel` 仅对 `cancel_supported=True` 且处于 `running/reported_running` 的运行生效。
3. running 结果的 `tool_run_id` 是模型引用一次后台运行的唯一标识（查询、取消都用它）。

### 3.3 Tool Run 状态机扩展

在现有状态机上新增 `cancelled` 终态：

```text
running           -> completed_inline | reported_running | failed | cancelled
reported_running  -> completed_late | failed | cancelled
completed_late    -> followed_up | expired
```

`cancelled` 与 `failed` 一样不触发模型 follow-up；可选地经 direct 通道播报一句"已取消"。

### 3.4 FollowUpRouter 的 direct 通道

`completion` 增加 `notify_policy` 字段（来自 `ToolSpec.late_result_notify`）：

```text
notify_policy=model   -> 现有行为（注入模型组织回复）
notify_policy=direct  -> OutputService 通知仲裁直通播报 ToolResult.message
                         （会话关闭时同样落待通知，唤醒后直通或注入由策略决定）
```

直通文案遵循既有错误文案规则：原始错误只落盘，播报只用安全文案。

## 4. 删除清单

代码（迁移完成后删除）：

1. `realtime_agent/tasks.py` 全部：`BaseTask`、`TaskSpec`、`TaskRef`、`TaskSignal`、`TaskEventView`、`TaskContext`、`TaskEngine`、`TaskRunner`、`TaskScheduler`、`TaskSignalBridge`、`TaskStore`/`JsonlTaskStore`、`TimerTask`、`TaskAutoDiscovery`、`TaskExecutor`。
2. `tools.py` 中的 `TaskStartTool`、`TaskRuntimeManagerTool`、`TaskDeviceFacade`（long-running 门面并入按策略注入逻辑）、`_task_*` 辅助函数。
3. `app.py` 中 TaskEngine 装配、task 自动发现注册、`_handle_device_command_report` 的 `command.*` → `task.event.*` 转换。
4. `__init__.py` / `preflight.py` / `cli/sdk.py` 的 Task 公共导出。
5. `task_store/` 目录与 `tasks.jsonl` / `task-signals.jsonl` 产物（runs 读取方同步更新）。

文档：

1. `TaskCore设计.md`、`TaskCore实施计划.md` 移入 `docs/deprecated/`，顶部注明被本文取代。
2. `docs/tutorials/build-first-capability.md` 重写："第一个 Tool 和 Task" → "第一个能力工具"，Task 章节改为 background Tool 示例。
3. ToolRun 设计 5.3 节修订（已不再"特例对待"TaskStartTool）。

兼容性说明：本仓库内无 SDK 外部消费者，公共 API 直接删除、测试同仓迁移；不做 deprecation shim。`TaskSignal` 唯一保留的语义（`allow_direct_notify` 直通）由 `late_result_notify="direct"` 承接。

## 5. 风险

1. **command 内联消费的健壮性**：actor 模型曾承诺"事件必达实例"；内联模式下工具协程异常退出会丢失订阅。缓解：`CommandHandle.results()` 的 broker 订阅在协程退出时显式 `stop()`（`finally` 约定），且 ToolRun 终态有统一记录；设备离线路径已有 `fail_device_commands` 唤醒。
2. **timer 重启丢失**：`asyncio.sleep` 不可恢复。现状的 schedule 恢复同样未落地，重启后 Tool Run 统一失败化 + 待通知"计时中断"，语义不回退。
3. **取消竞态**：cancel 与完成几乎同时到达时由 CAS 裁决，与窗口竞态同一套机制。
4. **迁移期双轨**：按实施计划分阶段，先补 Tool Run 能力（取消/direct 通道/manager），再迁 TimerTask，最后删 tasks.py；每阶段全量测试通过后才进入下一阶段。

## 6. 维护约束

本文是 Task 概念退役的依据文档。具体迁移步骤见 [Tool与Task概念统一实施计划.md](Tool与Task概念统一实施计划.md)；与 ToolRun 设计冲突时，以本文为准（ToolRun 设计 5.3 节同步修订）。
