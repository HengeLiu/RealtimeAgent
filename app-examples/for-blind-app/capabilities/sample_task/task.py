from __future__ import annotations

from audio_chat import BaseTask, TaskContext, TaskSignal


class ReminderTask(BaseTask):
    """最小 Task 样板。

    主要功能：演示长任务启动后通过 TaskSignalBridge 回流信号。
    主要方法：`on_start()` 发送一个 `task.sample.started` 事件。
    主要属性：`task_type` 是自动发现和 TaskEngine 创建任务时使用的稳定类型。
    """

    task_type = "sample_reminder"
    description = "样板提醒任务。"

    async def on_start(self, context: TaskContext) -> None:
        """处理任务启动。

        主要逻辑：通过 `context.bridge` 发出结构化任务信号，不直接操作设备或播放器。
        参数：`context` 为 SDK 注入的任务上下文。
        返回值：无。
        异常情况：没有 bridge 时不发送事件。
        """

        if context.bridge is None:
            return
        context.bridge.handle_signal(
            TaskSignal(
                task_id=context.task_ref.task_id,
                task_type=context.task_ref.task_type,
                signal_name="task.sample.started",
                user_id=context.user_id,
                session_id=context.session_id,
                payload={"summary": context.task_ref.summary},
                allow_direct_notify=False,
            )
        )
