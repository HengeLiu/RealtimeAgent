"""找物体手机处理器示例。"""

from __future__ import annotations

from typing import Any

from openaiglasses.phone import BasePhoneProcessor, PhoneProcessorContext


class YoloFindObjectProcessor(BasePhoneProcessor):
    """手机侧找物体处理器。

    主要功能：
    1. 接收眼镜视频帧。
    2. 执行目标检测。
    3. 输出结构化检测结果。

    主要方法：
    1. `on_frame`：处理一帧图像。
    """

    processor_type = "yolo_find_object"
    description = "手机侧 YOLO 找物体处理器示例"

    def on_frame(self, context: PhoneProcessorContext, frame: Any) -> None:
        """处理一帧图像。

        参数：
        1. `context`：手机处理器上下文。
        2. `frame`：端侧适配层传入的帧对象。

        返回值：
        1. 无。

        异常情况：
        1. 当前示例不主动抛出异常。
        """

        target_object = str(context.params.get("target_object") or "").strip()
        frame_text = str(frame)
        found = bool(target_object and target_object in frame_text)
        context.emit_result(
            {
                "event_name": "phone.vision.find_object.result",
                "target_object": target_object,
                "found": found,
                "confidence": 0.9 if found else 0.0,
                "position": "center" if found else "unknown",
                "summary": f"找到{target_object}了" if found else f"暂未找到{target_object}",
            }
        )
