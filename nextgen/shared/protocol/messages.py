"""协议消息对象定义。"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from nextgen.shared.enums.common import ChannelType, TransportMode
from nextgen.shared.models.base import SourceTargetRef
from nextgen.shared.models.error import ErrorInfo


class MessageType:
    """消息类型常量集合。

    主要功能：
    - 统一维护控制面和数据面所需的核心消息类型常量。
    """

    DEVICE_HELLO = "device.lifecycle.hello"
    DEVICE_HEARTBEAT = "device.lifecycle.heartbeat"
    EVENT_VOICE_REPORT = "event.voice.report"
    EVENT_DEVICE_STATE_REPORT = "event.device_state.report"
    TASK_COMMAND_START = "task.command.start"
    TASK_COMMAND_STOP = "task.command.stop"
    TASK_STATE_UPDATE = "task.state.update"
    CAPTURE_REQUEST_CREATE = "capture.request.create"
    CAPTURE_REQUEST_GRANTED = "capture.request.granted"
    CAPTURE_REQUEST_CANCEL = "capture.request.cancel"
    EXECUTION_REQUEST_PLAY = "execution.request.play"
    EXECUTION_FEEDBACK_REPORT = "execution.feedback.report"
    STREAM_OPEN = "stream.open"
    STREAM_FRAME_PUSH = "stream.frame.push"
    STREAM_FRAME_PULL = "stream.frame.pull"
    STREAM_CLOSE = "stream.close"
    LINK_PEER_PREPARE = "link.peer.prepare"
    LINK_PEER_READY = "link.peer.ready"
    ACK_MESSAGE = "ack.message"
    ERROR_MESSAGE = "error.message"


@dataclass
class AckMessage:
    """消息确认对象。

    主要功能：
    - 对需要确认的消息返回最小确认结果。
    """

    acked_message_id: str
    status: str = "ok"

    def to_dict(self) -> Dict[str, Any]:
        """将 ACK 对象转换为字典。"""

        return {
            "acked_message_id": self.acked_message_id,
            "status": self.status,
        }


@dataclass
class ErrorMessage:
    """错误消息对象。

    主要功能：
    - 表示某条消息处理失败，并附带标准错误定义。
    """

    failed_message_id: str
    error: ErrorInfo

    def to_dict(self) -> Dict[str, Any]:
        """将错误消息转换为字典。"""

        return {
            "failed_message_id": self.failed_message_id,
            "error": self.error.to_dict(),
        }


@dataclass
class DataFrameHeader:
    """数据帧头部定义。

    主要功能：
    - 为数据面帧提供轻量头信息，描述帧属于哪个任务、哪种流。
    """

    protocol_version: str
    message_id: str
    message_type: str
    channel: ChannelType
    transport_mode: TransportMode
    session_id: str
    source: SourceTargetRef
    target: SourceTargetRef
    payload_meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """将数据帧头转换为字典。"""

        return {
            "protocol_version": self.protocol_version,
            "message_id": self.message_id,
            "message_type": self.message_type,
            "channel": self.channel.value,
            "transport_mode": self.transport_mode.value,
            "session_id": self.session_id,
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "payload_meta": self.payload_meta,
        }


@dataclass
class StreamOpenPayload:
    """开启流的载荷定义。"""

    stream_id: str
    transport_mode: TransportMode
    stream_type: str
    direction: str

    def to_dict(self) -> Dict[str, Any]:
        """将开启流载荷转换为字典。"""

        return {
            "stream_id": self.stream_id,
            "transport_mode": self.transport_mode.value,
            "stream_type": self.stream_type,
            "direction": self.direction,
        }
