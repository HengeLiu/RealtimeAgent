"""感知总线实现。"""

from copy import deepcopy
from typing import Dict, List, Optional

from nextgen.apps.glass.sensors.input_sources import HardwareSensorSource, UiSimulationSensorSource

from nextgen.shared.contracts.sensor import SensorHub
from nextgen.shared.enums.common import TaskPriority
from nextgen.shared.models.capture import CaptureGrant, CaptureProfile, CaptureRequest
from nextgen.shared.models.base import Resolution


class GlassSensorHub(SensorHub):
    """眼镜端感知总线。

    主要功能：
    - 接收任务采集请求
    - 做最小采集仲裁和参数合并
    - 支持取消请求和查看当前有效配置
    """

    QUALITY_ORDER = {
        None: 0,
        "low": 1,
        "medium": 2,
        "high": 3,
    }

    def __init__(self) -> None:
        """初始化感知总线。"""

        self.requests: Dict[str, CaptureRequest] = {}
        self.sensor_index: Dict[str, List[str]] = {}
        self.ui_simulation_source = UiSimulationSensorSource()
        self.hardware_source = HardwareSensorSource()

    def register_capture_request(self, request: CaptureRequest) -> CaptureGrant:
        """注册采集请求。

        参数：
        - request：采集请求对象。

        返回值：
        - 一个默认授权通过的占位结果。
        """

        self.requests[request.request_id] = request
        sensor_key = request.sensor.value
        self.sensor_index.setdefault(sensor_key, [])
        if request.request_id not in self.sensor_index[sensor_key]:
            self.sensor_index[sensor_key].append(request.request_id)

        return CaptureGrant(
            request_id=request.request_id,
            granted=True,
            effective_profile=self.get_effective_profile(request.sensor.value),
            reason="merged_by_sensor_hub",
        )

    def cancel_capture_request(self, request_id: str) -> None:
        """取消采集请求。

        参数：
        - request_id：采集请求标识。
        """

        request = self.requests.pop(request_id, None)
        if request is None:
            return
        sensor_key = request.sensor.value
        request_ids = self.sensor_index.get(sensor_key, [])
        if request_id in request_ids:
            request_ids.remove(request_id)
        if not request_ids and sensor_key in self.sensor_index:
            self.sensor_index.pop(sensor_key, None)

    def list_active_requests(self, sensor: Optional[str] = None) -> List[CaptureRequest]:
        """列出当前生效的采集请求。"""

        if sensor is None:
            return [deepcopy(request) for request in self.requests.values()]
        request_ids = self.sensor_index.get(sensor, [])
        return [deepcopy(self.requests[request_id]) for request_id in request_ids if request_id in self.requests]

    def get_effective_profile(self, sensor: str) -> CaptureProfile:
        """获取某个采集器当前的生效采集参数。"""

        active_requests = self.list_active_requests(sensor=sensor)
        if not active_requests:
            return CaptureProfile()

        chosen_priority = max(active_requests, key=lambda item: self._priority_rank(item.priority))
        max_fps = max((request.profile.fps or 0) for request in active_requests) or None
        max_width = max(
            (request.profile.resolution.width for request in active_requests if request.profile.resolution),
            default=None,
        )
        max_height = max(
            (request.profile.resolution.height for request in active_requests if request.profile.resolution),
            default=None,
        )
        best_quality = max(
            (request.profile.quality for request in active_requests),
            key=lambda quality: self.QUALITY_ORDER.get(quality, 0),
        )
        max_duration = max((request.profile.duration_ms or 0) for request in active_requests) or None

        return CaptureProfile(
            fps=max_fps,
            resolution=Resolution(width=max_width, height=max_height) if max_width and max_height else None,
            quality=best_quality,
            duration_ms=max_duration,
            extra={"selected_priority": chosen_priority.priority.value},
        )

    def _priority_rank(self, priority: TaskPriority) -> int:
        """将任务优先级映射为可比较的数值。"""

        order = {
            TaskPriority.LOW: 1,
            TaskPriority.NORMAL: 2,
            TaskPriority.HIGH: 3,
            TaskPriority.URGENT: 4,
        }
        return order[priority]

    def configure_local_camera(self, camera_index: int = 0, preferred_width: Optional[int] = None, preferred_height: Optional[int] = None) -> None:
        """配置本机摄像头适配器。

        参数：
        - camera_index：摄像头编号
        - preferred_width：期望宽度
        - preferred_height：期望高度
        """

        self.hardware_source.configure_camera(
            camera_index=camera_index,
            preferred_width=preferred_width,
            preferred_height=preferred_height,
        )

    def capture_local_camera_frame(self, output_path: Optional[str] = None) -> Dict[str, object]:
        """采集一帧本机摄像头画面。

        参数：
        - output_path：可选输出文件路径

        返回值：
        - 摄像头采集结果字典

        异常情况：
        - 若尚未配置本机摄像头，则抛出 `RuntimeError`
        """

        return self.hardware_source.capture_camera_frame(output_path=output_path)

    def configure_local_microphone(self, sample_rate: int = 16000, channels: int = 1, dtype: str = "int16") -> None:
        """配置本机麦克风适配器。

        参数：
        - sample_rate：采样率
        - channels：声道数
        - dtype：采样数据类型
        """

        self.hardware_source.configure_microphone(
            sample_rate=sample_rate,
            channels=channels,
            dtype=dtype,
        )

    def record_local_microphone_audio(self, duration_sec: float, output_path: str) -> Dict[str, object]:
        """录制一段本机麦克风音频。

        参数：
        - duration_sec：录音时长
        - output_path：输出文件路径

        返回值：
        - 麦克风录制结果字典

        异常情况：
        - 若尚未配置本机麦克风，则抛出 `RuntimeError`
        """

        return self.hardware_source.record_microphone_audio(duration_sec=duration_sec, output_path=output_path)

    def start_local_microphone_recording(self, output_path: str) -> Dict[str, object]:
        """启动一段可停止的本机录音。"""

        return self.hardware_source.start_microphone_recording(output_path=output_path)

    def stop_local_microphone_recording(self) -> Dict[str, object]:
        """停止当前本机录音。"""

        return self.hardware_source.stop_microphone_recording()

    def start_local_microphone_stream(self, on_chunk, blocksize: int = 1600):
        """启动实时本机麦克风流。"""

        return self.hardware_source.start_microphone_stream(on_chunk=on_chunk, blocksize=blocksize)

    def inject_ui_text(self, text: str) -> Dict[str, object]:
        """通过 UI 模拟方式注入一段文本。"""

        return self.ui_simulation_source.inject_text(text)

    def inject_ui_image(self, image_path: str) -> Dict[str, object]:
        """通过 UI 模拟方式注入一张图片。"""

        return self.ui_simulation_source.inject_image_path(image_path)

    def inject_ui_video(self, video_path: str) -> Dict[str, object]:
        """通过 UI 模拟方式注入一段视频。"""

        return self.ui_simulation_source.inject_video_path(video_path)

    def build_input_snapshot(self) -> Dict[str, object]:
        """构造当前感知总线输入快照。"""

        return {
            "ui_simulation": self.ui_simulation_source.snapshot(),
            "hardware": self.hardware_source.snapshot(),
            "active_requests": [item.to_dict() for item in self.list_active_requests()],
        }

    @property
    def local_camera_device(self):
        """兼容旧调用方访问本机摄像头适配器。"""

        return self.hardware_source.local_camera_device

    @property
    def local_microphone_device(self):
        """兼容旧调用方访问本机麦克风适配器。"""

        return self.hardware_source.local_microphone_device
