"""事件模型定义。"""

from dataclasses import dataclass, field
from typing import Any, Dict

from nextgen.shared.enums.common import EventType


@dataclass
class VoiceEvent:
    """结构化语音事件。

    主要功能：
    - 描述经 VAD 和事件感知处理后的语音输入。
    """

    device_id: str
    timestamp: str
    text: str
    audio_ref: str
    vad_confidence: float
    event_type: EventType = EventType.VOICE_EVENT

    def to_dict(self) -> Dict[str, Any]:
        """将语音事件转换为字典。"""

        return {
            "event_type": self.event_type.value,
            "device_id": self.device_id,
            "timestamp": self.timestamp,
            "payload": {
                "text": self.text,
                "audio_ref": self.audio_ref,
                "vad_confidence": self.vad_confidence,
            },
        }


@dataclass
class DeviceStateEvent:
    """设备状态变化事件。"""

    device_id: str
    timestamp: str
    state_name: str
    value: Any
    event_type: EventType = EventType.DEVICE_STATE_CHANGED

    def to_dict(self) -> Dict[str, Any]:
        """将设备状态事件转换为字典。"""

        return {
            "event_type": self.event_type.value,
            "device_id": self.device_id,
            "timestamp": self.timestamp,
            "payload": {
                "state_name": self.state_name,
                "value": self.value,
            },
        }


@dataclass
class TaskStateEvent:
    """任务状态变化事件。"""

    session_id: str
    task_name: str
    status: str
    phase: str
    summary: Dict[str, Any] = field(default_factory=dict)
    event_type: EventType = EventType.TASK_STATE_CHANGED

    def to_dict(self) -> Dict[str, Any]:
        """将任务状态事件转换为字典。"""

        return {
            "event_type": self.event_type.value,
            "session_id": self.session_id,
            "payload": {
                "task_name": self.task_name,
                "status": self.status,
                "phase": self.phase,
                "summary": self.summary,
            },
        }
