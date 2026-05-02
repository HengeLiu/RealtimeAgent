"""计时器 Task。"""

from __future__ import annotations

from openaiglasses import BaseTask, TaskContext, TaskEvent


class TimerTask(BaseTask):
    """计时器任务。

    主要功能：
    1. 保存计时器时长、名称和剩余时间。
    2. 通过 SDK 通用调度器安排到点事件。
    3. 计时完成后先回流 Agent，由 Agent 决定如何提示用户。

    主要方法：
    1. `on_start`：启动计时器并进入运行态。
    2. `on_event`：处理 tick 和 finished 事件。
    3. `on_cancel`：取消计时器。
    """

    task_type = "timer_task"
    description = "可查询、可取消、可完成通知的计时器任务"
    terminal_event_requires_agent_decision = True
    terminal_event_allow_direct_notify = False
    terminal_event_priority = "high"

    def on_start(self, context: TaskContext) -> None:
        """启动计时器任务。

        参数：
        1. `context`：SDK 任务上下文。

        返回值：
        1. 无。

        异常情况：
        1. 时长小于等于 0 时抛出 `RuntimeError`，由 SDK 统一记录为任务启动失败。
        """

        duration_seconds = int(context.input.get("duration_seconds") or 0)
        if duration_seconds <= 0:
            raise RuntimeError("计时器时长必须大于 0")
        label = str(context.input.get("label") or "计时器").strip() or "计时器"
        notify_text = str(context.input.get("notify_text") or "").strip()
        enable_background_timer = bool(context.input.get("enable_background_timer", True))
        context.emit_state(
            "running",
            {
                "label": label,
                "duration_seconds": duration_seconds,
                "remaining_seconds": duration_seconds,
                "notify_text": notify_text,
                "enable_background_timer": enable_background_timer,
            },
        )
        context.device_group.submit_notification(
            text=f"{label}已开始，时长 {duration_seconds} 秒",
            priority="normal",
        )
        if enable_background_timer:
            schedule = context.schedule_event(
                delay_ms=duration_seconds * 1000,
                event_name="timer.finished",
                payload={
                    "label": label,
                    "duration_seconds": duration_seconds,
                    "notify_text": notify_text or f"{label}时间到了",
                },
                source="timer_task",
            )
            context.update({"finish_schedule": dict(schedule)})

    def on_event(self, context: TaskContext, event: TaskEvent) -> None:
        """处理计时器事件。

        参数：
        1. `context`：SDK 任务上下文。
        2. `event`：计时器事件。

        返回值：
        1. 无。

        异常情况：
        1. 不支持的事件会被忽略，不主动抛出异常。
        """

        if event.name == "timer.tick":
            remaining_seconds = max(0, int(event.payload.get("remaining_seconds", 0)))
            context.emit_state(
                "running",
                {
                    "remaining_seconds": remaining_seconds,
                    "last_tick": dict(event.payload),
                },
            )
            return

        if event.name == "timer.finished":
            label = str(context.data.get("label") or context.input.get("label") or "计时器")
            notify_text = (
                str(event.payload.get("notify_text") or context.data.get("notify_text") or "").strip()
                or f"{label}时间到了"
            )
            result = {
                "label": label,
                "duration_seconds": int(context.data.get("duration_seconds") or context.input.get("duration_seconds") or 0),
                "finished": True,
                "message": notify_text,
            }
            context.complete(result=result)

    def on_cancel(self, context: TaskContext) -> None:
        """取消计时器任务。

        参数：
        1. `context`：SDK 任务上下文。

        返回值：
        1. 无。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        label = str(context.data.get("label") or context.input.get("label") or "计时器")
        context.emit_state(
            "cancelled",
            {
                "cancel_reason": "user_cancelled",
                "label": label,
            },
        )
        context.device_group.submit_notification(text=f"{label}已取消", priority="normal")
