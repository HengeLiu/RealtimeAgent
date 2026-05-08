from __future__ import annotations

from audio_chat import BaseTask, TaskContext


class TrafficLightPhoneTask(BaseTask):
    """红绿灯手机视觉任务迁移样板。

    主要功能：
    1. 使用 event + stream 协议请求端侧执行红绿灯识别。
    2. 保持业务代码只依赖 `TaskContext.devices`，不直接访问 phone 连接。
    3. 由 phone mock 或 iOS 参考端上报 command 事件完成任务。
    """

    task_type = "traffic_light_phone_task"
    description = "通过端侧视觉任务识别红绿灯状态"
    timeout_seconds = 10

    async def on_start(self, context: TaskContext) -> None:
        """启动红绿灯视觉任务。

        主要逻辑：发布 `control.device.command.requested`，payload 只放语义参数和
        stream 需求，RGB 帧由端侧通过 `sensor.rgb` stream 上传。
        参数：`context` 为 SDK 注入任务上下文。
        返回值：无。
        异常情况：没有匹配端侧时把任务标记为失败。
        """

        if context.devices is None:
            await context.fail("缺少设备上下文")
            return
        input_data = dict(context.metadata.get("input") or {})
        result = context.devices.publish_event(
            "control.device.command.requested",
            payload={
                "command_name": "phone.task.start",
                "task_type": self.task_type,
                "task_id": context.task_ref.task_id,
                "session_id": context.session_id,
                "input": dict(input_data),
                "required_streams": [
                    {"stream_type": "sensor.rgb", "mode": "continuous", "format": "jpeg"},
                ],
            },
            require_capability=f"phone.task.{self.task_type}",
            selection="first_available",
        )
        if result.delivered_count <= 0:
            await context.fail("没有在线端侧可执行红绿灯视觉任务", payload={"matched_count": result.matched_count})
