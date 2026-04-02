"""眼镜接入层骨架实现。"""

from typing import Any

from nextgen.shared.contracts.gateway import Gateway


class GlassGateway(Gateway):
    """眼镜接入层。

    主要功能：
    - 承接眼镜到服务器或手机的连接能力。
    - 提供统一的消息发送和接收入口。
    """

    def __init__(self) -> None:
        """初始化眼镜接入层。"""

        self.connected = False

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

    def receive(self) -> Any:
        """接收消息。

        返回值：
        - 当前阶段返回空值，占位后续真实接收逻辑。
        """

        return None
