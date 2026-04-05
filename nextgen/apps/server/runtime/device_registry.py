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
    - 处理真实联调中“先 heartbeat 后 register”的竞态情况

    主要方法：
    - `register`：写入正式设备注册信息
    - `heartbeat`：刷新心跳，必要时自动补建占位设备

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
        - 如果设备已经注册，则刷新已有记录
        - 如果设备尚未注册，则根据心跳先创建一条占位记录，避免真实联调时因竞态抛错
        """

        stored = self.registrations.get(heartbeat.device_id)
        if stored is None:
            stored = {
                "device_id": heartbeat.device_id,
                "runtime": "unknown",
                "display_name": heartbeat.device_id,
                "endpoint": heartbeat.endpoint.to_dict()
                if heartbeat.endpoint is not None
                else {
                    "host": "unknown",
                    "port": 0,
                    "scheme": "http",
                    "base_path": "/device-api",
                },
                "capabilities": [],
                "online": True,
                "network_type": "unknown",
                "boot_id": "",
                "status": heartbeat.status,
                "created_from": "heartbeat_auto_upsert",
            }
            self.registrations[heartbeat.device_id] = stored
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
