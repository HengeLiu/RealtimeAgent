"""接入层抽象接口。"""

from abc import ABC, abstractmethod
from typing import Any


class Gateway(ABC):
    """接入层抽象接口。

    主要功能：
    - 统一约束眼镜、手机、服务器接入层的基本能力。

    主要方法：
    - connect：建立连接
    - disconnect：断开连接
    - send：发送消息
    - receive：接收消息
    """

    @abstractmethod
    def connect(self) -> None:
        """建立连接。

        异常情况：
        - 当底层连接失败时，应抛出异常或记录失败状态。
        """

    @abstractmethod
    def disconnect(self) -> None:
        """断开连接。"""

    @abstractmethod
    def send(self, message: Any) -> None:
        """发送消息。

        参数：
        - message：待发送对象，可以是协议包络或其他标准对象。
        """

    @abstractmethod
    def receive(self) -> Any:
        """接收消息。

        返回值：
        - 最近一条收到的消息对象。
        """
