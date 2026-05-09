from __future__ import annotations

from audio_chat import BaseTask, TaskContext, TaskEvent


class NotificationTask(BaseTask):
    """后台通知任务迁移样板。

    主要功能：
    1. 通过 TaskEventBridge 记录任务状态。
    2. 通过 Output Service 提交文本输出。
    3. 避免业务 Task 直接控制播放队列或喇叭 stream。
    """

    task_type = "notification_task"
    description = "提交一次结构化通知输出"

    async def on_start(self, context: TaskContext) -> None:
        """启动通知任务。

        主要逻辑：
        1. 从任务输入中读取通知文本和优先级。
        2. 通过 `await context.output.say()` 提交文本输出。
        3. 通过 TaskEventBridge 写入任务事件。

        参数：
        1. `context`：SDK 注入的任务上下文。

        返回值：
        1. 无。

        异常情况：
        1. 输出服务或事件桥接失败时由 Task Engine 记录失败。
        """

        input_data = dict(context.metadata.get("input") or {})
        text = str(input_data.get("text") or "任务已完成")
        priority = str(input_data.get("priority") or "normal")
        if context.devices is not None:
            await context.output.say(text, priority=priority)
        if context.bridge is not None:
            context.bridge.handle_event(
                TaskEvent(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    event_name="notification_task.notified",
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload={"text": text, "priority": priority},
                    priority=priority,
                    allow_direct_notify=False,
                )
            )
