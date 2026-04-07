from __future__ import annotations

from task.base import TaskBase, TaskResult


class TimerTask(TaskBase):
    task_type = "timer"

    def validate_input(self) -> None:
        duration = self.context.input_context.get("duration_seconds")
        if not isinstance(duration, int) or duration <= 0:
            raise ValueError("duration_seconds must be a positive integer")

    def prepare(self) -> None:
        self.context.runtime_context["prepared"] = True

    def run(self) -> TaskResult:
        duration = self.context.input_context["duration_seconds"]
        notify_message = self.context.input_context.get("notify_message", "计时结束")
        self.context.result_context = {"duration_seconds": duration, "notify_message": notify_message}
        return TaskResult(summary="timer_finished", data=self.context.result_context)
