"""手机接入层实现。"""

from collections import deque
from copy import deepcopy
from typing import Any, Deque, Dict, List, Optional

from nextgen.shared.contracts.gateway import Gateway


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
