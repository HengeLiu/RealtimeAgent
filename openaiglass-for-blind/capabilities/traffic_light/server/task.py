"""红绿灯识别 Task。"""

from __future__ import annotations

from openaiglasses import BaseTask, TaskContext, TaskEvent


class TrafficLightTask(BaseTask):
    """红绿灯识别任务。

    主要功能：
    1. 启动眼镜到手机的视频链路。
    2. 请求手机侧处理器识别红绿灯状态。
    3. 根据结构化识别结果向眼镜提交安全提示，并按策略结束任务。

    主要方法：
    1. `on_start`：启动视频链路与手机任务。
    2. `on_event`：处理手机侧红绿灯识别事件。
    3. `on_cancel`：取消任务并释放视频链路和手机任务。
    """

    task_type = "traffic_light_task"
    description = "通过手机本地视觉处理器识别红绿灯状态"

    def on_start(self, context: TaskContext) -> None:
        """启动红绿灯识别任务。

        参数：
        1. `context`：任务上下文。

        返回值：
        1. 无。

        异常情况：
        1. 当前设备组缺少在线手机或视频链路适配器时由 SDK 统一记录为任务启动失败。
        """

        crossing_name = str(context.input.get("crossing_name") or "").strip()
        stop_after_first_signal = bool(context.input.get("stop_after_first_signal", True))
        params = {
            "crossing_name": crossing_name,
            "processor_type": "traffic_light_detector",
            "stop_after_first_signal": stop_after_first_signal,
        }
        context.device_group.start_phone_video_link(
            reason="traffic_light",
            params=params,
        )
        context.device_group.start_phone_task(
            task_type="traffic_light_phone_task",
            params=params,
        )
        context.emit_state(
            "running",
            {
                "crossing_name": crossing_name,
                "stop_after_first_signal": stop_after_first_signal,
            },
        )

    def on_event(self, context: TaskContext, event: TaskEvent) -> None:
        """处理手机侧红绿灯识别事件。

        参数：
        1. `context`：任务上下文。
        2. `event`：手机侧识别事件。

        返回值：
        1. 无。

        异常情况：
        1. 事件载荷缺少信号字段时保持任务运行，不主动抛出异常。
        """

        if event.name != "phone.vision.traffic_light.result":
            return
        signal = str(event.payload.get("signal") or "unknown").strip() or "unknown"
        context.update({"last_signal": event.payload})
        if signal == "unknown":
            return

        summary = str(event.payload.get("summary") or self._build_summary(signal))
        priority = "critical" if signal in {"red", "yellow"} else "high"
        context.device_group.submit_notification(text=summary, priority=priority)

        if bool(context.input.get("stop_after_first_signal", True)):
            context.device_group.stop_phone_task(
                task_type="traffic_light_phone_task",
                reason="task.completed",
            )
            context.device_group.stop_phone_video_link(reason="traffic_light_completed")
            context.complete(result=event.payload)

    def on_cancel(self, context: TaskContext) -> None:
        """取消红绿灯识别任务。

        参数：
        1. `context`：任务上下文。

        返回值：
        1. 无。

        异常情况：
        1. 释放视频链路或手机任务失败时记录错误，不让取消动作中断。
        """

        stop_result: dict[str, object]
        try:
            stop_result = context.device_group.stop_phone_video_link(reason="traffic_light_cancelled")
        except Exception as exc:
            stop_result = {"ok": False, "message": str(exc)}
        try:
            context.device_group.stop_phone_task(
                task_type="traffic_light_phone_task",
                reason="task.cancelled",
            )
        except Exception as exc:
            stop_result = {**stop_result, "phone_task_stop_error": str(exc)}
        context.emit_state(
            "cancelled",
            {
                "cancel_reason": "user_cancelled",
                "video_link_stop_result": stop_result,
            },
        )

    @staticmethod
    def _build_summary(signal: str) -> str:
        """根据红绿灯状态生成默认提示。"""

        if signal == "green":
            return "前方绿灯，可以谨慎通过"
        if signal == "yellow":
            return "前方黄灯，请暂缓通过"
        if signal == "red":
            return "前方红灯，请停下等待"
        return "暂未识别到明确红绿灯状态"

