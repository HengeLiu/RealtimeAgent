"""服务器接入层骨架实现。"""

from typing import Any

from nextgen.shared.contracts.gateway import Gateway


class ServerGateway(Gateway):
    """服务器接入层。

    主要功能：
    - 承接眼镜和手机的控制面消息。
    - 占位服务器侧消息路由和接入能力。
    """

    def __init__(self) -> None:
        """初始化服务器接入层。"""

        self.connected = True

    def connect(self) -> None:
        """服务器端默认视为已就绪。"""

    def disconnect(self) -> None:
        """断开服务器接入层。"""

        self.connected = False

    def send(self, message: Any) -> None:
        """发送消息。"""

        if not self.connected:
            raise RuntimeError("服务器接入层不可用，无法发送消息。")

    def receive(self) -> Any:
        """接收消息。"""

        return None
