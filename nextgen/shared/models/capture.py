"""采集模型定义。"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from nextgen.shared.enums.common import CaptureMode, SensorType, TaskPriority
from nextgen.shared.models.base import Resolution, SourceTargetRef


@dataclass
class CaptureProfile:
    """采集参数定义。

    主要功能：
    - 统一描述帧率、分辨率、质量和扩展采集参数。
    """

    fps: Optional[int] = None
    resolution: Optional[Resolution] = None
    quality: Optional[str] = None
    duration_ms: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """将采集参数转换为字典。"""

        return {
            "fps": self.fps,
            "resolution": self.resolution.to_dict() if self.resolution else None,
            "quality": self.quality,
            "duration_ms": self.duration_ms,
            "extra": self.extra,
        }


@dataclass
class CaptureRequest:
    """采集请求定义。

    主要功能：
    - 表示任务向感知总线申请某种采集能力。
    """

    request_id: str
    session_id: str
    sensor: SensorType
    mode: CaptureMode
    priority: TaskPriority
    profile: CaptureProfile
    consumer: SourceTargetRef

    def to_dict(self) -> Dict[str, Any]:
        """将采集请求转换为字典。"""

        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "sensor": self.sensor.value,
            "mode": self.mode.value,
            "priority": self.priority.value,
            "profile": self.profile.to_dict(),
            "consumer": self.consumer.to_dict(),
        }


@dataclass
class CaptureGrant:
    """采集授权结果。

    主要功能：
    - 描述感知总线仲裁采集请求后的实际生效结果。
    """

    request_id: str
    granted: bool
    effective_profile: CaptureProfile
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """将采集授权结果转换为字典。"""

        return {
            "request_id": self.request_id,
            "granted": self.granted,
            "effective_profile": self.effective_profile.to_dict(),
            "reason": self.reason,
        }
