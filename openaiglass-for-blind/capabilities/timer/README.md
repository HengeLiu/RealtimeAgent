# timer

计时器能力用于验证后台任务最小闭环。业务侧只使用 SDK Tool、Task、通用调度和通知能力；自然到点由 SDK 调度器触发，不在业务侧自建线程。

当前实现：

1. `start_timer` Tool 创建 `timer_task`。
2. `timer_task` 保存计时时长、剩余时间和完成提示。
3. `enable_background_timer=true` 时，通过 `TaskContext.schedule_event(...)` 安排 `timer.finished`。
4. `timer.finished` 后完成任务，并通过 `terminal_event_requires_agent_decision=True` 让终态事件先回流 Agent。
5. 启动和取消提示继续通过 SDK 通知能力提交。

当前限制：

1. SDK 调度器当前是单进程运行时能力，不代表已经具备跨进程分布式定时调度。
2. 到点语音播报依赖 Agent 回流和 TTS/眼镜播放链路，真机或回放测试时需要同时观察服务端 `assistant.reply`、`actuator.audio.play` 和眼镜端播放完成事件。
