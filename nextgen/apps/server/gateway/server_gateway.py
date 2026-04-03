"""服务器接入层实现。"""

from collections import deque
from copy import deepcopy
from typing import Any, Deque, Dict, List, Optional

from nextgen.shared.contracts.gateway import Gateway


class ServerGateway(Gateway):
    """服务器接入层。

    主要功能：
    - 承接眼镜和手机的控制面消息
    - 管理已连接客户端和消息收发缓冲
    - 承接从旧 `app_main.py` 迁移出的状态广播与 UI 文本承接能力
    """

    def __init__(self) -> None:
        """初始化服务器接入层。"""

        self.connected = True
        self.clients: Dict[str, Dict[str, Any]] = {}
        self.inbox: Deque[Any] = deque()
        self.outbox: Deque[Any] = deque()
        self.current_partial: str = ""
        self.recent_finals: List[str] = []
        self.recent_limit = 50
        self.runtime = None

    def connect(self) -> None:
        """服务器端默认视为已就绪。"""

    def disconnect(self) -> None:
        """断开服务器接入层。"""

        self.connected = False

    def send(self, message: Any) -> None:
        """发送消息。"""

        if not self.connected:
            raise RuntimeError("服务器接入层不可用，无法发送消息。")
        self.outbox.append(deepcopy(message))

    def receive(self) -> Any:
        """接收消息。"""

        if not self.inbox:
            return None
        return self.inbox.popleft()

    def register_client(self, runtime: str, device_id: str, component: Optional[str] = None) -> str:
        """注册一个接入客户端。"""

        client_key = self._make_client_key(runtime=runtime, device_id=device_id, component=component)
        self.clients[client_key] = {
            "runtime": runtime,
            "device_id": device_id,
            "component": component,
            "online": True,
        }
        return client_key

    def attach_runtime(self, runtime: Any) -> None:
        """挂接服务器运行时。"""

        self.runtime = runtime

    def unregister_client(self, runtime: str, device_id: str, component: Optional[str] = None) -> None:
        """注销一个接入客户端。"""

        client_key = self._make_client_key(runtime=runtime, device_id=device_id, component=component)
        self.clients.pop(client_key, None)

    def list_clients(self) -> List[Dict[str, Any]]:
        """列出当前在线客户端。"""

        return [deepcopy(item) for item in self.clients.values()]

    def push_incoming_message(self, message: Any) -> None:
        """压入一条收到的消息。"""

        self.inbox.append(deepcopy(message))

    def broadcast_partial_text(self, text: str) -> Dict[str, Any]:
        """广播当前 ASR partial 文本。"""

        self.current_partial = text
        payload = {"message_type": "ui.partial", "text": text}
        self.send(payload)
        return payload

    def broadcast_final_text(self, text: str) -> Dict[str, Any]:
        """广播当前 final 文本并写入最近历史。"""

        if text:
            self.recent_finals.append(text)
            self.recent_finals = self.recent_finals[-self.recent_limit :]
        payload = {"message_type": "ui.final", "text": text}
        self.send(payload)
        return payload

    def get_ui_state(self) -> Dict[str, Any]:
        """获取当前 UI 状态快照。"""

        return {
            "current_partial": self.current_partial,
            "recent_finals": list(self.recent_finals),
        }

    def _make_client_key(self, runtime: str, device_id: str, component: Optional[str]) -> str:
        """构造客户端唯一键。"""

        return f"{runtime}:{device_id}:{component or 'default'}"
