"""找物体手机任务示例。"""

from __future__ import annotations

from typing import Any

from openaiglasses.phone import BasePhoneTask, PhoneTaskContext


class FindObjectPhoneTask(BasePhoneTask):
    """手机侧找物体任务。

    主要功能：
    1. 维护找物体手机任务生命周期。
    2. 把输入帧交给指定处理器。
    3. 产出结构化结果供服务器任务或测试回放消费。
    """

    task_type = "find_object_phone_task"
    description = "手机侧找物体连续处理任务"

    def on_start(self, context: PhoneTaskContext) -> None:
        """启动手机侧找物体任务。"""

        processor_type = str(context.params.get("processor_type") or "yolo_find_object").strip() or "yolo_find_object"
        heading_sensor_type = str(context.params.get("heading_sensor_type") or "").strip()
        context.emit_state(
            "running",
            {
                "processor_type": processor_type,
                "target_object": str(context.params.get("target_object") or "").strip(),
                "heading_sensor_type": heading_sensor_type,
            },
        )

    def on_frame(self, context: PhoneTaskContext, frame: Any) -> None:
        """处理一帧找物体输入。"""

        processor_type = str(context.data.get("processor_type") or context.params.get("processor_type") or "").strip()
        if not processor_type:
            raise RuntimeError("手机找物体任务缺少 processor_type")
        result = context.process_frame(
            processor_type=processor_type,
            frame=frame,
            params=context.params,
        )
        heading_sensor_type = str(context.data.get("heading_sensor_type") or "").strip()
        if result and heading_sensor_type:
            reading = context.read_sensor(heading_sensor_type)
            result["heading_degrees"] = reading.payload.get("heading_degrees")
            result["heading_timestamp_ms"] = reading.timestamp_ms
        if result:
            context.emit_result(result)
            context.update({"last_result": result})
