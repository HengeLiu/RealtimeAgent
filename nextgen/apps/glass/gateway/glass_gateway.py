"""眼镜接入层实现。"""

from collections import deque
from copy import deepcopy
from typing import Any, Deque, Dict, List, Optional

from nextgen.shared.contracts.gateway import Gateway


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
