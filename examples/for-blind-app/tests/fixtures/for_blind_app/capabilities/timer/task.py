from __future__ import annotations

from realtime_agent.tasks import BaseTask, TaskContext, TaskSignal


class TimerTask(BaseTask):
    """测试用计时器 Task。"""

    task_type = "timer"

    async def on_start(self, context: TaskContext) -> None:
        """测试目标：验证 Task 输出必须走 Output Service。"""

        if context.devices is not None:
            await context.output.say("timer started", priority="normal")
        if context.bridge is not None:
            context.bridge.handle_signal(
                TaskSignal(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    signal_name="timer.started",
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload={"ok": True},
                    allow_direct_notify=False,
                )
            )
