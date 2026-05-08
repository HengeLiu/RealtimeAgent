"""红绿灯识别手机任务。"""

from __future__ import annotations

from typing import Any

from openaiglasses.phone import BasePhoneTask, PhoneTaskContext


class TrafficLightPhoneTask(BasePhoneTask):
    """手机侧红绿灯识别任务。

    主要功能：
    1. 维护手机侧红绿灯识别任务生命周期。
    2. 把输入帧交给指定处理器。
    3. 保存最近一次结构化识别结果。
    """

    task_type = "traffic_light_phone_task"
    description = "手机侧红绿灯连续识别任务"

    def on_start(self, context: PhoneTaskContext) -> None:
        """启动手机侧红绿灯识别任务。

        参数：
        1. `context`：手机任务上下文。

        返回值：
        1. 无。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        processor_type = str(context.params.get("processor_type") or "traffic_light_detector").strip()
        context.emit_state(
            "running",
            {
                "processor_type": processor_type or "traffic_light_detector",
                "crossing_name": str(context.params.get("crossing_name") or "").strip(),
            },
        )

    def on_frame(self, context: PhoneTaskContext, frame: Any) -> None:
        """处理一帧红绿灯输入。

        参数：
        1. `context`：手机任务上下文。
        2. `frame`：视频帧。

        返回值：
        1. 无。

        异常情况：
        1. 缺少处理器类型时抛出 `RuntimeError`，由 SDK 回放或运行时暴露问题。
        """

        processor_type = str(context.data.get("processor_type") or context.params.get("processor_type") or "").strip()
        if not processor_type:
            raise RuntimeError("手机红绿灯任务缺少 processor_type")
        result = context.process_frame(
            processor_type=processor_type,
            frame=frame,
            params=context.params,
        )
        if result:
            context.emit_result(result)
            context.update({"last_result": result})
