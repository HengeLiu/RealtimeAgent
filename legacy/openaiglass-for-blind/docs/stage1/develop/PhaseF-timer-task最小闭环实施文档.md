# Phase F timer_task 最小闭环实施文档

## 1. 目标

Phase F 的目标是用真实业务能力验证 SDK `backend-task-core`：业务侧只实现 `start_timer` Tool 和 `timer_task`，计时器创建、状态查询、取消、事件分发和完成通知都走 SDK 托管任务运行时。

当前实现基于 `sdk-v84` 的 `TaskContext.schedule_event(...)` 和终态事件回流策略。业务侧不再使用 `threading.Timer`，计时到点由 SDK 调度器触发 `timer.finished`，任务完成后先回流 Agent，再由 Agent 决定如何通知用户。

## 2. 实现范围

代码目录：

1. `capabilities/timer/server/tool.py`
2. `capabilities/timer/server/task.py`
3. `capabilities/timer/README.md`
4. `host/server/main.py`

能力注册：

1. `StartTimerTool` 注册为 `start_timer`。
2. `TimerTask` 注册为 `timer_task`。
3. 不再注册组件级场景处理器；设备级回放统一由 `glass-playback` 和 SDK 测试工具承载。

## 3. 业务行为

`start_timer`：

1. 输入 `duration_seconds`、`label`、`notify_text`。
2. 校验 `duration_seconds > 0`。
3. 通过 `context.create_task("timer_task", ...)` 创建 SDK 托管任务。
4. 返回 `task_id`、任务状态和任务数据。

`timer_task`：

1. `on_start` 进入 `running`，记录总时长和剩余时长，并提交启动通知。
2. `timer.tick` 更新 `remaining_seconds`。
3. `enable_background_timer=true` 时，通过 `context.schedule_event(...)` 安排 `timer.finished`。
4. `timer.finished` 写入 `message` 并 `complete`，终态事件由 SDK 回流 Agent。
5. `on_cancel` 进入 `cancelled` 并提交取消通知。

## 4. 流程图

```plantuml
@startuml
title timer_task 最小闭环

actor User as user
participant "Agent / Scenario" as agent
participant "StartTimerTool" as tool
participant "DeviceGroupContext" as ctx
participant "SDK TaskRuntime" as runtime
participant "SDK Scheduler" as scheduler
participant "TimerTask" as task
participant "Agent" as agent2
participant "Glass Playback" as glass

user -> agent: 创建三分钟计时器
agent -> tool: start_timer(duration_seconds=180)
tool -> ctx: create_task("timer_task")
ctx -> runtime: create_task()
runtime -> task: on_start()
task -> glass: submit_notification("计时器已开始")
task -> scheduler: schedule_event("timer.finished", delay_ms=180000)
... 到点 ...
scheduler -> runtime: dispatch_event("timer.finished")
runtime -> task: on_event()
task -> runtime: complete(result)
runtime -> agent2: task.completed(message="时间到了")
agent2 -> glass: assistant.reply + actuator.audio.play

@enduml
```

## 5. 场景覆盖

新增场景：

1. `testdata/scenario/timer_finished.json`：成功创建并通过 `timer.finished` 完成。
2. `testdata/scenario/timer_cancelled.json`：创建后通过 `task.cancel` 取消。
3. `testdata/scenario/timer_invalid_duration.json`：非法时长返回结构化 Tool 错误。
4. `testdata/scenario/timer_running_tick.json`：通过 `timer.tick` 推进剩余时间，任务保持运行态。

测试目标：

1. 成功路径能创建 SDK 托管任务并完成通知。
2. 失败路径不创建任务，返回 `invalid_input`。
3. 取消路径能进入 `cancelled` 并通知用户。
4. 事件推进路径能更新任务数据，便于后续查询。
5. SDK 调度事件到点后任务能进入 `completed`，并通过终态事件策略回流 Agent。

## 6. 联调说明

真机联调时，计时器不依赖手机视觉插件。推荐流程：

1. 启动业务服务端并打开 DEBUG 日志。
2. 启动眼镜端，确认控制连接、心跳和通知播放链路可用。
3. 通过语音或调试入口触发 `start_timer`。
4. 服务端观察 `timer_task` 创建、状态变化和通知记录。
5. 眼镜端观察启动通知、取消通知或完成通知。

当前 SDK 调度器可覆盖本地单进程自然到点验证；进程重启恢复、多实例租约和跨机器调度仍不是业务侧能力。

## 7. 当前测试结果

建议执行：

```bash
python -m compileall capabilities host/server/main.py
uv run openaiglass.sdk.preflight --report logs/sdk-preflight-current.json
```

当前组件级场景回放入口已删除；后续统一使用 `glass-playback`、`phone-mock` 和 SDK 预检做设备级验证。

本轮已执行：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind:. uv run python -m pytest openaiglass-for-blind/tests/test_capabilities_unit.py -q
```

结果：通过，覆盖 SDK 调度器安排到点事件、任务完成和终态 Agent 回流策略。

2026-05-02 设备级回放：

```bash
uv run python -m pytest openaiglass-for-blind/tests -q
LOG_LEVEL=DEBUG uv run openaiglass.server.start --app-module host.server.main --app-root openaiglass-for-blind --config openaiglass-for-blind/config/local_server.env --log-file openaiglass-for-blind/logs/server-timer-3s-voice-check.log
PYTHONPATH=openaiglass-sdk/glass-playback uv run openaiglass.glass.start --runtime playback --config /tmp/openaiglass_timer_3s_voice_check.json --timeout-seconds 120 --max-runtime-seconds 120
```

本次使用 macOS `say` 临时合成 `/tmp/openaiglass_timer_3s.wav`，内容为“帮我设置一个三秒钟的计时器”，不提交到仓库。关键结果：

1. 服务端识别到 `start_timer(duration_seconds=3)`。
2. `timer_task` 生成 `finish_schedule`，由 SDK 调度器安排 `timer.finished`。
3. 到点后 Agent 收到 `task.completed`，回复“计时器时间到了。”。
4. `runs/playback/timer-3s-voice-check/events.jsonl` 记录到点后的 `assistant.reply`。
5. `runs/playback/timer-3s-voice-check/actuators.jsonl` 记录到点后的 `actuator.audio.play`。

结果：通过，用户可以在到点后收到语音播报。
