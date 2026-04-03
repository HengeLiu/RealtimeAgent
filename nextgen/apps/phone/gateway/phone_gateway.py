"""手机接入层实现。"""

from collections import deque
from copy import deepcopy
from typing import Any, Deque, Dict, List, Optional

from nextgen.shared.contracts.gateway import Gateway
from nextgen.shared.enums.common import LinkStatus, RuntimeType
from nextgen.shared.models.control import DeviceHeartbeat, DeviceRegistration, NodeEndpoint


class PhoneGateway(Gateway):
    """手机接入层。

    主要功能：
    - 承接手机到服务器、眼镜的数据与控制消息
    - 维护与眼镜的点对点会话状态
    """

    def __init__(self) -> None:
        """初始化手机接入层。"""

        self.connected = False
        self.peer_sessions: Dict[str, Dict[str, Any]] = {}
        self.inbox: Deque[Any] = deque()
        self.outbox: Deque[Any] = deque()
        self.control_endpoint: Optional[NodeEndpoint] = None
        self.device_id: str = "phone-001"

    def connect(self) -> None:
        """建立连接。"""

        self.connected = True

    def disconnect(self) -> None:
        """断开连接。"""

        self.connected = False

    def send(self, message: Any) -> None:
        """发送消息。"""

        if not self.connected:
            raise RuntimeError("手机接入层尚未连接，无法发送消息。")
        self.outbox.append(deepcopy(message))

    def receive(self) -> Any:
        """接收消息。"""

        if not self.inbox:
            return None
        return self.inbox.popleft()

    def push_incoming_message(self, message: Any) -> None:
        """压入一条收到的消息。"""

        self.inbox.append(deepcopy(message))

    def open_peer_session(self, session_id: str, peer_device_id: str, mode: str = "data") -> Dict[str, Any]:
        """打开一个点对点会话。"""

        session = {
            "session_id": session_id,
            "peer_device_id": peer_device_id,
            "mode": mode,
            "status": "open",
        }
        self.peer_sessions[session_id] = session
        return deepcopy(session)

    def close_peer_session(self, session_id: str) -> None:
        """关闭一个点对点会话。"""

        self.peer_sessions.pop(session_id, None)

    def list_peer_sessions(self) -> List[Dict[str, Any]]:
        """列出当前点对点会话。"""

        return [deepcopy(session) for session in self.peer_sessions.values()]

    def update_control_endpoint(self, endpoint: NodeEndpoint) -> None:
        """更新控制面节点地址。"""

        self.control_endpoint = endpoint

    def build_registration(self, display_name: str, capabilities: list, boot_id: str = "") -> DeviceRegistration:
        """构造设备注册对象。"""

        if self.control_endpoint is None:
            raise ValueError("手机控制面地址尚未配置。")
        return DeviceRegistration(
            device_id=self.device_id,
            runtime=RuntimeType.PHONE,
            display_name=display_name,
            endpoint=self.control_endpoint,
            capabilities=capabilities,
            boot_id=boot_id,
        )

    def build_heartbeat(self, status: str = "ready", payload: Optional[Dict[str, Any]] = None) -> DeviceHeartbeat:
        """构造设备心跳。"""

        return DeviceHeartbeat(
            device_id=self.device_id,
            status=status,
            endpoint=self.control_endpoint,
            payload=payload or {},
        )

    def prepare_peer_link_listener(self, task_session_id: str, peer_device_id: str, stream_type: str) -> Dict[str, Any]:
        """准备任务级监听入口。"""

        if self.control_endpoint is None:
            raise ValueError("手机控制面地址尚未配置。")
        listen_endpoint = NodeEndpoint(
            host=self.control_endpoint.host,
            port=self.control_endpoint.port,
            scheme="ws",
            base_path=f"/peer-link/{task_session_id}",
        )
        session = {
            "session_id": task_session_id,
            "peer_device_id": peer_device_id,
            "stream_type": stream_type,
            "listen_endpoint": listen_endpoint.to_dict(),
            "status": LinkStatus.LISTENING.value,
        }
        self.peer_sessions[task_session_id] = session
        return deepcopy(session)
