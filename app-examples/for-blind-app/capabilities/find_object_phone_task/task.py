from __future__ import annotations

from audio_chat import BaseTask, TaskContext


class FindObjectPhoneTask(BaseTask):
    """找物手机视觉任务。

    主要功能：
    1. 用 `context.devices.commands.start()` 请求具备 phone task 能力的端侧执行找物。
    2. 要求视觉帧仍通过 `sensor.rgb` stream 上传，不把图片塞进控制事件。
    3. 等待 phone mock 或 iOS 参考端通过 command 回报驱动任务完成。
    """

    task_type = "find_object_phone_task"
    description = "通过端侧视觉任务寻找指定目标"
    timeout_seconds = 10

    async def on_start(self, context: TaskContext) -> None:
        """启动找物手机视觉任务。

        主要逻辑：从任务输入读取目标名称，调用 typed command API；SDK App 会把端侧
        `command.*` 回报桥接回 TaskEngine。
        参数：`context` 为 SDK 注入任务上下文。
        返回值：无。
        异常情况：没有匹配端侧时把任务标记为失败。
        """

        if context.devices is None:
            await context.fail("缺少设备上下文")
            return
        input_data = dict(context.metadata.get("input") or {})
        try:
            await context.devices.commands.start(
                name="phone.task.start",
                selector={"device_role": "phone"},
                params={
                    "task_type": self.task_type,
                    "task_id": context.task_ref.task_id,
                    "session_id": context.session_id,
                    "input": {
                        "target": str(input_data.get("target") or input_data.get("object_name") or "目标物"),
                        **input_data,
                    },
                    "required_streams": [
                        {"stream_type": "sensor.rgb", "mode": "continuous", "format": "jpeg"},
                    ],
                },
            )
        except Exception as exc:
            await context.fail("没有在线端侧可执行找物视觉任务", payload={"error": str(exc)})
