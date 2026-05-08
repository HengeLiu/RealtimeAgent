"""红绿灯识别手机处理器。"""

from __future__ import annotations

from typing import Any

from openaiglasses.phone import BasePhoneProcessor, PhoneProcessorContext


class TrafficLightProcessor(BasePhoneProcessor):
    """手机侧红绿灯识别处理器。

    主要功能：
    1. 接收眼镜视频帧。
    2. 从帧内容中识别红、黄、绿灯状态。
    3. 输出结构化识别结果，供服务端 Task 推进状态。

    主要方法：
    1. `on_frame`：处理一帧图像或回放文本。
    """

    processor_type = "traffic_light_detector"
    description = "手机侧红绿灯识别处理器"

    def on_frame(self, context: PhoneProcessorContext, frame: Any) -> None:
        """处理一帧红绿灯输入。

        参数：
        1. `context`：手机处理器上下文。
        2. `frame`：端侧适配层传入的视频帧对象。

        返回值：
        1. 无。

        异常情况：
        1. 当前处理器不主动抛出异常；无法识别时返回 `unknown`。
        """

        frame_text = str(frame).lower()
        signal = self._detect_signal(frame_text)
        context.emit_result(
            {
                "event_name": "phone.vision.traffic_light.result",
                "signal": signal,
                "confidence": self._confidence(signal),
                "summary": self._summary(signal),
                "crossing_name": str(context.params.get("crossing_name") or "").strip(),
            }
        )

    @staticmethod
    def _detect_signal(frame_text: str) -> str:
        """从文本化帧内容中提取红绿灯状态。"""

        if any(token in frame_text for token in ["green", "绿灯", "绿色"]):
            return "green"
        if any(token in frame_text for token in ["yellow", "黄灯", "黄色"]):
            return "yellow"
        if any(token in frame_text for token in ["red", "红灯", "红色"]):
            return "red"
        return "unknown"

    @staticmethod
    def _confidence(signal: str) -> float:
        """根据识别状态返回示例置信度。"""

        return 0.92 if signal != "unknown" else 0.0

    @staticmethod
    def _summary(signal: str) -> str:
        """根据识别状态生成用户提示。"""

        if signal == "green":
            return "前方绿灯，可以谨慎通过"
        if signal == "yellow":
            return "前方黄灯，请暂缓通过"
        if signal == "red":
            return "前方红灯，请停下等待"
        return "暂未识别到明确红绿灯状态"
