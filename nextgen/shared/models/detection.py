"""检测与引导模型定义。"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


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
