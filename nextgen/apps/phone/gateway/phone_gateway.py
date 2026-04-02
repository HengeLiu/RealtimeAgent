"""手机接入层骨架实现。"""

from typing import Any

from nextgen.shared.contracts.gateway import Gateway


class PhoneGateway(Gateway):
    """手机接入层。

    主要功能：
    - 承接手机到服务器、眼镜的数据与控制消息。
    """

    def __init__(self) -> None:
        """初始化手机接入层。"""

        self.connected = False

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

    def receive(self) -> Any:
        """接收消息。"""

        return None
