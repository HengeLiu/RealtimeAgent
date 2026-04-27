"""找物体 Task 示例。"""

from __future__ import annotations

from openaiglasses import BaseTask, TaskContext, TaskEvent


class FindObjectTask(BaseTask):
    """找物体任务。

    主要功能：
    1. 启动眼镜到手机的视频链路。
    2. 请求手机侧处理器寻找目标物体。
    3. 根据手机侧结构化结果完成任务。

    主要方法：
    1. `on_start`：任务启动。
    2. `on_event`：处理手机侧检测事件。
    3. `on_cancel`：任务取消。
    """

    task_type = "find_object_task"
    description = "通过手机本地视觉处理器寻找目标物体"

    def on_start(self, context: TaskContext) -> None:
        """启动找物体任务。

        参数：
        1. `context`：任务上下文。

        返回值：
        1. 无。

        异常情况：
        1. 当前设备组缺少手机或视频链路适配器时由 SDK 抛出异常。
        """

        target_object = str(context.input.get("target_object") or "").strip()
        frame_interval_ms = int(context.input.get("frame_interval_ms") or 500)
        target_ws_uri = str(context.input.get("target_ws_uri") or "").strip()
        context.device_group.start_phone_video_link(
            reason="find_object",
            params={
                "target_object": target_object,
                "processor_type": "yolo_find_object",
                "frame_interval_ms": frame_interval_ms,
                "target_ws_uri": target_ws_uri,
            },
        )
        context.device_group.start_phone_task(
            task_type="find_object_phone_task",
            params={
                "target_object": target_object,
                "processor_type": "yolo_find_object",
            },
        )
        context.emit_state("running", {"target_object": target_object})

    def on_event(self, context: TaskContext, event: TaskEvent) -> None:
        """处理找物体检测事件。

        参数：
        1. `context`：任务上下文。
        2. `event`：手机侧检测事件。

        返回值：
        1. 无。

        异常情况：
        1. 事件载荷缺少字段时保持任务运行，不主动抛出异常。
        """

        if event.name != "phone.vision.find_object.result":
            return
        context.update({"last_detection": event.payload})
        if event.payload.get("found"):
            context.device_group.stop_phone_task(
                task_type="find_object_phone_task",
                reason="task.completed",
            )
            context.device_group.stop_phone_video_link(reason="find_object_completed")
            context.device_group.submit_notification(
                text=str(event.payload.get("summary") or "找到目标了"),
                priority="high",
            )
            context.complete(result=event.payload)

    def on_cancel(self, context: TaskContext) -> None:
        """取消找物体任务。

        参数：
        1. `context`：任务上下文。

        返回值：
        1. 无。

        异常情况：
        1. 如果视频链路已经不可用，则记录取消状态并保留错误信息，不让取消动作失败。
        """

        stop_result: dict[str, object]
        try:
            stop_result = context.device_group.stop_phone_video_link(reason="find_object_cancelled")
        except Exception as exc:
            stop_result = {
                "ok": False,
                "message": str(exc),
            }
        try:
            context.device_group.stop_phone_task(
                task_type="find_object_phone_task",
                reason="task.cancelled",
            )
        except Exception as exc:
            stop_result = {
                **stop_result,
                "phone_task_stop_error": str(exc),
            }
        context.emit_state(
            "cancelled",
            {
                "cancel_reason": "user_cancelled",
                "video_link_stop_result": stop_result,
            },
        )
