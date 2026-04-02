"""感知总线实现。"""

from copy import deepcopy
from typing import Dict, List, Optional

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
