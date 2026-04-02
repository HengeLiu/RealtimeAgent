"""统一消息包络定义。"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from nextgen.shared.enums.common import ChannelType
from nextgen.shared.models.base import SourceTargetRef


@dataclass
class Envelope:
    """统一消息包络。

    主要功能：
    - 为所有控制面消息提供统一的协议外层。

    主要属性：
    - protocol_version：协议版本
    - message_id：消息唯一标识
    - message_type：消息类型
    - channel：消息所处通道
    - timestamp：发送时间
    - trace_id：链路追踪标识
    - source：发送方
    - target：接收方
    - session_id：所属任务实例
    - requires_ack：是否要求确认
    - payload：业务载荷
    """

    protocol_version: str
    message_id: str
    message_type: str
    channel: ChannelType
    timestamp: str
    trace_id: str
    source: SourceTargetRef
    target: SourceTargetRef
    session_id: Optional[str] = None
    requires_ack: bool = False
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """将消息包络转换为字典。"""

        return {
            "protocol_version": self.protocol_version,
            "message_id": self.message_id,
            "message_type": self.message_type,
            "channel": self.channel.value,
            "timestamp": self.timestamp,
            "trace_id": self.trace_id,
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "session_id": self.session_id,
            "requires_ack": self.requires_ack,
            "payload": self.payload,
        }
