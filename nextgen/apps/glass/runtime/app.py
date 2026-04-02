"""眼镜端运行时应用实现。"""

from dataclasses import dataclass

from nextgen.apps.glass.execution.device_control import GlassDeviceControl
from nextgen.apps.glass.execution.executor_bus import GlassExecutorBus
from nextgen.apps.glass.gateway.glass_gateway import GlassGateway
from nextgen.apps.glass.sensors.event_detector import GlassEventDetector
from nextgen.apps.glass.sensors.sensor_hub import GlassSensorHub

@dataclass
class GlassRuntimeApp:
    """眼镜端运行时应用。

    主要功能：
    - 组合眼镜端接入层、感知模块和执行模块。

    主要属性：
    - name：运行时名称
    """

    name: str = "glass-runtime"
    device_id: str = "glass-001"

    def start(self) -> None:
        """启动眼镜端运行时。

        主要逻辑：
        - 当前阶段完成最小模块装配，便于后续扩展真实启动逻辑。
        """

        self.gateway = GlassGateway()
        self.sensor_hub = GlassSensorHub()
        self.event_detector = GlassEventDetector(device_id=self.device_id)
        self.executor_bus = GlassExecutorBus()
        self.device_control = GlassDeviceControl()
        self.gateway.connect()

    def build_voice_event(self, text: str, audio_ref: str, confidence: float):
        """基于当前事件感知模块构造语音事件。"""

        if not self.event_detector.should_emit_voice_event(text, confidence):
            return None
        return self.event_detector.build_voice_event(text, audio_ref, confidence)

    def stop(self) -> None:
        """停止眼镜端运行时。

        主要逻辑：
        - 当前阶段只保留停止入口，占位后续资源释放逻辑。
        """

        self.gateway.disconnect()
