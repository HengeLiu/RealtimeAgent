"""目标检测技能骨架实现。"""

from dataclasses import dataclass
from datetime import datetime

from nextgen.shared.models.detection import DetectionResult


@dataclass
class ObjectDetectionSkill:
    """目标检测技能。

    主要功能：
    - 为寻找物体任务提供统一的检测结果占位输出。

    当前阶段：
    - 不接真实模型，只构造一个未检测到目标的占位结果。
    """

    def detect(self, session_id: str, target_name: str) -> DetectionResult:
        """执行目标检测。

        参数：
        - session_id：任务实例标识
        - target_name：目标名称

        返回值：
        - 一个占位检测结果。
        """

        return DetectionResult(
            session_id=session_id,
            result_type="object_detection",
            timestamp=datetime.now().astimezone().isoformat(),
            target_name=target_name,
            found=False,
            position="unknown",
            score=0.0,
        )
