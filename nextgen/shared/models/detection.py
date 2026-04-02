"""检测与引导模型定义。"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class BoundingBox:
    """检测框定义。"""

    x1: int
    y1: int
    x2: int
    y2: int

    def to_dict(self) -> Dict[str, Any]:
        """将检测框转换为字典。"""

        return {
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
        }


@dataclass
class HandObservation:
    """手部观测结果。"""

    center_x: float
    center_y: float
    area: float
    bbox: BoundingBox
    grasp_detected: bool = False
    grasp_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """将手部观测结果转换为字典。"""

        return {
            "center_x": self.center_x,
            "center_y": self.center_y,
            "area": self.area,
            "bbox": self.bbox.to_dict(),
            "grasp_detected": self.grasp_detected,
            "grasp_score": self.grasp_score,
        }


@dataclass
class ObjectObservation:
    """目标物体观测结果。"""

    center_x: float
    center_y: float
    area: float
    polygon: List[List[float]] = field(default_factory=list)
    score: float = 0.0
    position: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        """将目标观测结果转换为字典。"""

        return {
            "center_x": self.center_x,
            "center_y": self.center_y,
            "area": self.area,
            "polygon": self.polygon,
            "score": self.score,
            "position": self.position,
        }


@dataclass
class FindObjectFrameAnalysis:
    """寻找物体任务的单帧分析输入。

    主要功能：
    - 承接手机侧单帧分析得到的中间结果
    - 让旧 `yolomedia.py` 的零散变量可以先归一化，再进入新技能接口
    """

    frame_width: int
    frame_height: int
    target_name: str
    found: bool
    object_observation: Optional[ObjectObservation] = None
    hand_observation: Optional[HandObservation] = None
    candidate_count: int = 0
    source: str = "legacy_yolomedia"

    def to_dict(self) -> Dict[str, Any]:
        """将单帧分析输入转换为字典。"""

        return {
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "target_name": self.target_name,
            "found": self.found,
            "object_observation": self.object_observation.to_dict() if self.object_observation else None,
            "hand_observation": self.hand_observation.to_dict() if self.hand_observation else None,
            "candidate_count": self.candidate_count,
            "source": self.source,
        }


@dataclass
class DetectionResult:
    """检测结果定义。

    主要功能：
    - 统一描述目标检测或其他识别技能的输出结果。
    """

    session_id: str
    result_type: str
    timestamp: str
    target_name: str
    found: bool
    position: str
    score: float
    bbox: Optional[BoundingBox] = None
    distance_level: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """将检测结果转换为字典。"""

        return {
            "session_id": self.session_id,
            "result_type": self.result_type,
            "timestamp": self.timestamp,
            "payload": {
                "target_name": self.target_name,
                "found": self.found,
                "position": self.position,
                "score": self.score,
                "bbox": self.bbox.to_dict() if self.bbox else None,
                "distance_level": self.distance_level,
                "extra": self.extra,
            },
        }


@dataclass
class HintPayload:
    """引导建议定义。

    主要功能：
    - 表示任务组件为用户生成的引导内容，常由手机直接发送给眼镜。
    """

    session_id: str
    hint_type: str
    text: str
    priority: str

    def to_dict(self) -> Dict[str, Any]:
        """将引导建议转换为字典。"""

        return {
            "session_id": self.session_id,
            "hint_type": self.hint_type,
            "text": self.text,
            "priority": self.priority,
        }
