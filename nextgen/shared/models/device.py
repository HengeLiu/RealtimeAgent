"""设备模型定义。"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from nextgen.shared.enums.common import CapabilityType, RuntimeType


@dataclass
class NetworkStatus:
    """网络状态。

    主要功能：
    - 描述设备在控制面和对等连接上的当前状态。
    """

    control_connected: bool = False
    peer_connected: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """将对象转换为字典。"""

        return asdict(self)


@dataclass
class DeviceInfo:
    """设备信息。

    主要功能：
    - 描述设备基础信息、在线状态和能力集合。

    主要属性：
    - device_id：设备唯一标识
    - runtime：运行时类型
    - display_name：展示名称
    - online：当前在线状态
    - capabilities：能力集合
    - network：网络状态
    - last_seen_at：最近心跳时间
    """

    device_id: str
    runtime: RuntimeType
    display_name: str
    online: bool
    capabilities: List[CapabilityType] = field(default_factory=list)
    network: NetworkStatus = field(default_factory=NetworkStatus)
    last_seen_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """将设备对象转换为字典。"""

        return {
            "device_id": self.device_id,
            "runtime": self.runtime.value,
            "display_name": self.display_name,
            "online": self.online,
            "capabilities": [item.value for item in self.capabilities],
            "network": self.network.to_dict(),
            "last_seen_at": self.last_seen_at,
        }
