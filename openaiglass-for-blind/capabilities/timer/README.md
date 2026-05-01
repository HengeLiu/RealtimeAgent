# timer

计时器能力用于验证后台任务最小闭环。业务侧只使用 SDK Tool、Task 和通知能力，当前为了验证“自然到点”场景，在 `timer_task` 内使用轻量 `threading.Timer` 推进到点事件。

当前实现：

1. `start_timer` Tool 创建 `timer_task`。
2. `timer_task` 保存计时时长、剩余时间和完成提示。
3. `enable_background_timer=true` 时，到点后自动触发 `timer.finished` 并完成任务。
4. 任务完成或取消后通过 SDK 通知能力提交提示。

当前限制：

1. SDK 公开任务桥接层尚未提供“定时调度”和“任务事件必须回流 Agent 决策”的业务接口。
2. 因此本实现可以验证自然到点和用户通知，但还不能严格做到“先主动通知 Agent，再由 Agent 决定如何通知用户”。
