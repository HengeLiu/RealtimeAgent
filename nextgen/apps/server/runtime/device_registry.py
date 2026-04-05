"""服务器端设备注册表。"""

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional

from nextgen.shared.models.control import DeviceHeartbeat, DeviceRegistration


class DeviceRegistry:
    """维护设备注册、心跳和节点地址。

    主要功能：
    - 接收正式注册信息
    - 接收设备心跳并刷新在线状态

    主要方法：
    - `register`：写入正式设备注册信息
    - `heartbeat`：刷新已注册设备的心跳

    主要属性：
    - `registrations`：当前已知设备状态表
    """

    def __init__(self) -> None:
        self.registrations: Dict[str, Dict[str, Any]] = {}

    def register(self, registration: DeviceRegistration) -> Dict[str, Any]:
        """注册或刷新设备信息。"""

        stored = registration.to_dict()
        stored["last_seen_at"] = datetime.now().astimezone().isoformat()
        self.registrations[registration.device_id] = stored
        return deepcopy(stored)

    def heartbeat(self, heartbeat: DeviceHeartbeat) -> Dict[str, Any]:
        """应用设备心跳。

        主要逻辑：
        - 设备必须已经完成注册
        - 未注册设备发送心跳时直接报错，避免协议约束被弱化
        """

        stored = self.registrations.get(heartbeat.device_id)
        if stored is None:
            raise KeyError(f"设备未注册: {heartbeat.device_id}")
        stored["status"] = heartbeat.status
        stored["last_seen_at"] = heartbeat.sent_at
        stored["online"] = True
        if heartbeat.endpoint is not None:
            stored["endpoint"] = heartbeat.endpoint.to_dict()
        if heartbeat.payload:
            stored["last_heartbeat_payload"] = deepcopy(heartbeat.payload)
        return deepcopy(stored)

    def get(self, device_id: str) -> Optional[Dict[str, Any]]:
        """获取单个设备。"""

        stored = self.registrations.get(device_id)
        return deepcopy(stored) if stored else None

    def list_all(self) -> List[Dict[str, Any]]:
        """列出所有设备。"""

        return [deepcopy(item) for item in self.registrations.values()]
