"""控制面与任务级连接模型。"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from nextgen.shared.enums.common import CapabilityType, LinkStatus, RuntimeType


@dataclass
class NodeEndpoint:
    """设备节点地址。"""

    host: str
    port: int
    scheme: str = "http"
    base_path: str = "/device-api"

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""

        return {
            "host": self.host,
            "port": self.port,
            "scheme": self.scheme,
            "base_path": self.base_path,
        }

    def as_base_url(self) -> str:
        """生成基础 URL。"""

        return f"{self.scheme}://{self.host}:{self.port}{self.base_path}"


@dataclass
class DeviceRegistration:
    """设备注册信息。"""

    device_id: str
    runtime: RuntimeType
    display_name: str
    endpoint: NodeEndpoint
    capabilities: List[CapabilityType] = field(default_factory=list)
    online: bool = True
    network_type: str = "wifi"
    boot_id: str = ""
    status: str = "ready"
    registered_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""

        return {
            "device_id": self.device_id,
            "runtime": self.runtime.value,
            "display_name": self.display_name,
            "endpoint": self.endpoint.to_dict(),
            "capabilities": [item.value for item in self.capabilities],
            "online": self.online,
            "network_type": self.network_type,
            "boot_id": self.boot_id,
            "status": self.status,
            "registered_at": self.registered_at,
        }


@dataclass
class DeviceHeartbeat:
    """设备心跳。"""

    device_id: str
    status: str = "ready"
    endpoint: Optional[NodeEndpoint] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    sent_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""

        return {
            "device_id": self.device_id,
            "status": self.status,
            "endpoint": self.endpoint.to_dict() if self.endpoint else None,
            "payload": self.payload,
            "sent_at": self.sent_at,
        }


@dataclass
class PeerLinkState:
    """任务级点对点连接状态。"""

    task_session_id: str
    glass_device_id: str
    phone_device_id: str
    stream_type: str
    status: LinkStatus = LinkStatus.PENDING
    phone_listen_endpoint: Optional[NodeEndpoint] = None
    glass_status: LinkStatus = LinkStatus.PENDING
    phone_status: LinkStatus = LinkStatus.PENDING
    connect_attempt_count: int = 0
    last_error: Optional[str] = None
    updated_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""

        return {
            "task_session_id": self.task_session_id,
            "glass_device_id": self.glass_device_id,
            "phone_device_id": self.phone_device_id,
            "stream_type": self.stream_type,
            "status": self.status.value,
            "phone_listen_endpoint": self.phone_listen_endpoint.to_dict() if self.phone_listen_endpoint else None,
            "glass_status": self.glass_status.value,
            "phone_status": self.phone_status.value,
            "connect_attempt_count": self.connect_attempt_count,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }
