"""执行器控制骨架实现。"""

from typing import Dict

from nextgen.apps.glass.hardware.local_devices import LocalSpeakerDevice
from nextgen.shared.contracts.execution import DeviceControl


class GlassDeviceControl(DeviceControl):
    """眼镜端执行器控制模块。

    主要功能：
    - 管理喇叭和振动器相关的配置项。
    """

    def __init__(self) -> None:
        """初始化执行器控制模块。"""

        self.settings: dict = {
            "speaker_volume": 50,
            "vibration_strength": 50,
            "mute": False,
        }
        self.local_speaker_device: LocalSpeakerDevice | None = None

    def configure(self, settings: dict) -> None:
        """应用执行器配置。

        参数：
        - settings：执行器配置字典。
        """

        self.settings.update(settings)

    def get_settings(self) -> dict:
        """获取当前执行器配置快照。"""

        return dict(self.settings)

    def enable_local_speaker(self) -> None:
        """启用本机喇叭适配器。"""

        self.local_speaker_device = LocalSpeakerDevice(volume=int(self.settings.get("speaker_volume", 50)))

    def execute_speech(self, text: str) -> Dict[str, object]:
        """执行一次本机语音播报。

        参数：
        - text：待播报文本

        返回值：
        - 播报执行结果字典
        """

        if self.settings.get("mute", False):
            return {"speaker_backend": "mute", "pid": None, "text": text}
        if self.local_speaker_device is None:
            return {"speaker_backend": "noop", "pid": None, "text": text}
        self.local_speaker_device.volume = int(self.settings.get("speaker_volume", 50))
        return self.local_speaker_device.speak_text(text)
