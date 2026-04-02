"""寻找物体任务组件骨架实现。"""

from dataclasses import dataclass

from nextgen.shared.models.detection import DetectionResult, HintPayload


@dataclass
class FindObjectTask:
    """寻找物体任务组件。

    主要功能：
    - 接收目标检测结果
    - 生成最小引导建议

    当前阶段：
    - 只提供最小状态和建议生成占位，不实现真实检测闭环。
    """

    target_name: str
    phase: str = "waiting_stream"

    def update_from_detection(self, result: DetectionResult) -> HintPayload:
        """根据检测结果生成引导建议。

        主要逻辑：
        - 若检测到目标，则生成一个最小引导建议。
        - 若未检测到目标，则给出继续扫描提示。

        参数：
        - result：目标检测结果。

        返回值：
        - 面向用户的引导建议对象。
        """

        if result.found:
            self.phase = "guiding"
            text = f"检测到{self.target_name}，位置：{result.position}"
        else:
            self.phase = "scanning"
            text = f"尚未检测到{self.target_name}，继续扫描"
        return HintPayload(
            session_id=result.session_id,
            hint_type="guidance",
            text=text,
            priority="high",
        )
