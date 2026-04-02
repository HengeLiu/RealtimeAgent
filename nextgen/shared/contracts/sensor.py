"""感知相关抽象接口。"""

from abc import ABC, abstractmethod

from nextgen.shared.models.capture import CaptureGrant, CaptureRequest
from nextgen.shared.models.event import VoiceEvent


class SensorHub(ABC):
    """感知总线抽象接口。

    主要功能：
    - 管理采集请求
    - 仲裁采集参数
    - 释放采集资源
    """

    @abstractmethod
    def register_capture_request(self, request: CaptureRequest) -> CaptureGrant:
        """注册采集请求。

        参数：
        - request：任务发起的采集请求。

        返回值：
        - 感知总线给出的采集授权结果。
        """

    @abstractmethod
    def cancel_capture_request(self, request_id: str) -> None:
        """取消采集请求。

        参数：
        - request_id：需要取消的采集请求标识。
        """


class EventDetector(ABC):
    """事件感知抽象接口。

    主要功能：
    - 对原始输入做事件化处理
    - 输出结构化事件
    """

    @abstractmethod
    def build_voice_event(self, text: str, audio_ref: str, confidence: float) -> VoiceEvent:
        """构造语音事件。

        参数：
        - text：语音文本
        - audio_ref：原始音频引用
        - confidence：VAD 或相关置信度

        返回值：
        - 结构化语音事件对象。
        """
