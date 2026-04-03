"""眼镜接入层实现。"""

from collections import deque
from copy import deepcopy
from typing import Any, Deque, Dict, List, Optional

from nextgen.shared.contracts.gateway import Gateway
from nextgen.shared.enums.common import LinkStatus, RuntimeType
from nextgen.shared.models.control import DeviceHeartbeat, DeviceRegistration, NodeEndpoint


class GlassGateway(Gateway):
    """眼镜接入层。

    主要功能：
    - 承接眼镜到服务器或手机的连接能力
    - 提供统一的消息发送和接收入口
    - 维护控制面与点对点会话状态
    """

    def __init__(self) -> None:
        """初始化眼镜接入层。"""

        self.connected = False
        self.peer_sessions: Dict[str, Dict[str, Any]] = {}
        self.inbox: Deque[Any] = deque()
        self.outbox: Deque[Any] = deque()
        self.control_endpoint: Optional[NodeEndpoint] = None
        self.device_id: str = "glass-001"

    def connect(self) -> None:
        """建立连接。"""

        self.connected = True

    def disconnect(self) -> None:
        """断开连接。"""

        self.connected = False

    def send(self, message: Any) -> None:
        """发送消息。

        参数：
        - message：待发送对象。
        """

        if not self.connected:
            raise RuntimeError("眼镜接入层尚未连接，无法发送消息。")
        self.outbox.append(deepcopy(message))

    def receive(self) -> Any:
        """接收消息。

        返回值：
        - 当前阶段返回空值，占位后续真实接收逻辑。
        """

        if not self.inbox:
            return None
        return self.inbox.popleft()

    def push_incoming_message(self, message: Any) -> None:
        """压入一条收到的消息。"""

        self.inbox.append(deepcopy(message))

    def open_peer_session(self, session_id: str, peer_device_id: str, mode: str = "control") -> Dict[str, Any]:
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
            raise ValueError("眼镜控制面地址尚未配置。")
        return DeviceRegistration(
            device_id=self.device_id,
            runtime=RuntimeType.GLASS,
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

    def connect_peer_link(self, task_session_id: str, peer_device_id: str, peer_endpoint: NodeEndpoint, stream_type: str) -> Dict[str, Any]:
        """建立任务级连接。"""

        session = {
            "session_id": task_session_id,
            "peer_device_id": peer_device_id,
            "peer_endpoint": peer_endpoint.to_dict(),
            "stream_type": stream_type,
            "mode": "peer_data",
            "status": LinkStatus.CONNECTED.value,
        }
        self.peer_sessions[task_session_id] = session
        return deepcopy(session)

    def report_broken_peer_link(self, task_session_id: str, reason: str) -> Dict[str, Any]:
        """标记任务级连接异常断开。"""

        session = self.peer_sessions.setdefault(task_session_id, {"session_id": task_session_id})
        session["status"] = LinkStatus.BROKEN.value
        session["last_error"] = reason
        return deepcopy(session)
