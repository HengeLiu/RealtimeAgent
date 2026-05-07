from __future__ import annotations

from audio_chat import BaseTask, TaskContext, TaskEvent


class TimerTask(BaseTask):
    """后台计时器示例 Task。

    主要功能：
    1. 展示 Task 启动后如何通过 `UserDeviceContext.submit_text()` 提交输出。
    2. 展示 TaskEvent 如何通过 bridge 回流，不直接操作端侧连接。
    3. 展示取消时如何提交取消事件和通知。
    """

    task_type = "timer"
    description = "最小后台计时器任务"

    async def on_start(self, context: TaskContext) -> None:
        """启动计时器。

        主要逻辑：本示例不等待真实时间，只冻结 Task / Output 闭环；真实计时器可在
        后续 TaskScheduler 中补充延时调度。
        参数：`context` 为 SDK 注入的任务上下文。
        返回值：无。
        异常情况：输出或事件记录失败时向上抛出。
        """

        seconds = int(context.metadata.get("input", {}).get("seconds", 1))
        if context.devices is not None:
            context.devices.submit_text(f"{seconds} 秒计时器已启动", priority="normal")
        if context.bridge is not None:
            context.bridge.handle_event(
                TaskEvent(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    event_name="timer.started",
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload={"seconds": seconds, "message": "timer started"},
                    allow_direct_notify=False,
                )
            )

    async def on_cancel(self, context: TaskContext) -> None:
        """取消计时器。

        主要逻辑：通过 Output Service 提交取消提示，不直接向某个设备发送命令。
        参数：`context` 为 SDK 注入的任务上下文。
        返回值：无。
        异常情况：输出失败时向上抛出。
        """

        if context.devices is not None:
            context.devices.submit_text("计时器已取消", priority="normal")
