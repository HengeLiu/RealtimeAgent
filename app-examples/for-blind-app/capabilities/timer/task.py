from __future__ import annotations

from audio_chat import BaseTask, TaskContext, TaskSignal


class TimerTask(BaseTask):
    """计时器 Task。

    主要功能：
    1. 使用 `TaskContext.schedule_signal()` 表达到点信号。
    2. 支持取消通知。
    3. 到点提示进入 Output Service，不直接控制播放器。
    """

    task_type = "timer_task"
    description = "计时器后台任务"

    async def on_start(self, context: TaskContext) -> None:
        """启动计时器。

        主要逻辑：记录 scheduled 信号，提交启动提示，再调度 `timer.due`。
        参数：`context` 为 SDK 注入上下文。
        返回值：无。
        异常情况：调度或输出失败时由 TaskEngine 记录。
        """

        input_data = dict(context.metadata.get("input") or {})
        seconds = max(0, int(input_data.get("seconds") or 0))
        auto_fire = bool(input_data.get("auto_fire", True))
        if context.devices is not None:
            await context.output.say(f"{seconds} 秒计时器已启动", priority="normal")
        if context.bridge is not None:
            context.bridge.handle_signal(
                TaskSignal(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    signal_name="timer.scheduled",
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload={"seconds": seconds},
                    allow_direct_notify=False,
                )
            )
        if auto_fire:
            await context.schedule_signal(
                "timer.due",
                payload={"seconds": seconds, "message": f"{seconds} 秒计时器到点了"},
                delay_seconds=seconds,
                priority="high",
                requires_agent_decision=False,
                allow_direct_notify=True,
            )

    async def on_signal(self, context: TaskContext, signal: TaskSignal) -> None:
        """处理计时器信号。"""

        if signal.signal_name != "timer.due":
            return
        seconds = int(signal.payload.get("seconds") or 0)
        await context.complete({"seconds": seconds, "notified": True}, summary="计时器到点")

    async def on_cancel(self, context: TaskContext) -> None:
        """取消计时器。"""

        if context.devices is not None:
            await context.output.say("计时器已取消", priority="normal")
