# timer

计时器能力用于验证后台任务最小闭环。业务侧只使用 SDK Tool、Task、通知和场景回放能力，不创建线程，也不绕过任务运行时。

当前实现：

1. `start_timer` Tool 创建 `timer_task`。
2. `timer_task` 保存计时时长、剩余时间和完成提示。
3. 离线回放通过 `timer.tick`、`timer.finished`、`task.cancel` 推进任务。
4. 任务完成或取消后通过 SDK 通知能力提交提示。
