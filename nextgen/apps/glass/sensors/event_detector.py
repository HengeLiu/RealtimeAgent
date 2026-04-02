"""事件感知骨架实现。"""

from datetime import datetime

from nextgen.shared.contracts.sensor import EventDetector
from nextgen.shared.models.event import VoiceEvent


class GlassEventDetector(EventDetector):
    """眼镜端事件感知模块。

    主要功能：
    - 将 VAD 后的语音文本和音频引用封装成结构化语音事件。
    """

    def __init__(self, device_id: str) -> None:
        """初始化事件感知模块。

        参数：
        - device_id：当前眼镜设备标识。
        """

        self.device_id = device_id

    def build_voice_event(self, text: str, audio_ref: str, confidence: float) -> VoiceEvent:
        """构造语音事件。

        参数：
        - text：语音文本
        - audio_ref：音频引用
        - confidence：置信度

        返回值：
        - 结构化语音事件对象。
        """

        return VoiceEvent(
            device_id=self.device_id,
            timestamp=datetime.now().astimezone().isoformat(),
            text=text,
            audio_ref=audio_ref,
            vad_confidence=confidence,
        )
