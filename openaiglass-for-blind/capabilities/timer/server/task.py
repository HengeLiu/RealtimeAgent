"""计时器 Task。"""

from __future__ import annotations

import threading
from typing import ClassVar

from openaiglasses import BaseTask, TaskContext, TaskEvent


class TimerTask(BaseTask):
    """计时器任务。

    主要功能：
    1. 保存计时器时长、名称和剩余时间。
    2. 通过业务侧轻量倒计时或外部事件推进计时状态。
    3. 计时完成或取消时通过 SDK 通知能力提示用户。

    主要方法：
    1. `on_start`：启动计时器并进入运行态。
    2. `on_event`：处理 tick 和 finished 事件。
    3. `on_cancel`：取消计时器。
    """

    task_type = "timer_task"
    description = "可查询、可取消、可完成通知的计时器任务"
    _timers: ClassVar[dict[str, threading.Timer]] = {}
    _timers_lock: ClassVar[threading.Lock] = threading.Lock()

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
            self._schedule_finish_event(context=context, duration_seconds=duration_seconds)

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
            notify_text = str(context.data.get("notify_text") or "").strip() or f"{label}时间到了"
            result = {
                "label": label,
                "duration_seconds": int(context.data.get("duration_seconds") or context.input.get("duration_seconds") or 0),
                "finished": True,
            }
            context.device_group.submit_notification(text=notify_text, priority="high")
            context.complete(result=result)
            self._clear_timer(context.task_id)

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
        self._cancel_timer(context.task_id)
        context.emit_state(
            "cancelled",
            {
                "cancel_reason": "user_cancelled",
                "label": label,
            },
        )
        context.device_group.submit_notification(text=f"{label}已取消", priority="normal")

    def _schedule_finish_event(self, *, context: TaskContext, duration_seconds: int) -> None:
        """启动业务侧倒计时。

        参数：
        1. `context`：SDK 任务上下文，倒计时结束后仍使用该上下文推进任务状态。
        2. `duration_seconds`：倒计时时长。

        返回值：
        1. 无。

        异常情况：
        1. 本函数不主动抛出异常；后台线程内异常会被捕获并记录到任务数据。
        """

        timer = threading.Timer(duration_seconds, self._finish_from_background, kwargs={"context": context})
        timer.daemon = True
        with self._timers_lock:
            previous = self._timers.pop(context.task_id, None)
            if previous is not None:
                previous.cancel()
            self._timers[context.task_id] = timer
        timer.start()

    def _finish_from_background(self, *, context: TaskContext) -> None:
        """倒计时到点后推进任务完成。

        参数：
        1. `context`：任务启动时 SDK 提供的上下文。

        返回值：
        1. 无。

        异常情况：
        1. 通知失败时记录到任务数据，避免后台线程异常退出影响服务端。
        """

        try:
            if context.state in {"completed", "cancelled", "failed", "timeout"}:
                return
            self.on_event(
                context,
                TaskEvent(
                    name="timer.finished",
                    payload={"trigger": "background_timer", "task_id": context.task_id},
                    source="timer_task",
                ),
            )
        except Exception as exc:  # pragma: no cover - 后台线程保护
            context.update({"background_timer_error": str(exc)})

    @classmethod
    def _cancel_timer(cls, task_id: str) -> None:
        """取消指定任务的后台倒计时。"""

        with cls._timers_lock:
            timer = cls._timers.pop(task_id, None)
        if timer is not None:
            timer.cancel()

    @classmethod
    def _clear_timer(cls, task_id: str) -> None:
        """清理已完成任务的倒计时句柄。"""

        with cls._timers_lock:
            cls._timers.pop(task_id, None)
