"""感知总线骨架实现。"""

from nextgen.shared.contracts.sensor import SensorHub
from nextgen.shared.models.capture import CaptureGrant, CaptureRequest


class GlassSensorHub(SensorHub):
    """眼镜端感知总线。

    主要功能：
    - 接收任务采集请求。
    - 返回统一的采集授权结果。
    - 占位后续仲裁、复用和降级逻辑。
    """

    def register_capture_request(self, request: CaptureRequest) -> CaptureGrant:
        """注册采集请求。

        参数：
        - request：采集请求对象。

        返回值：
        - 一个默认授权通过的占位结果。
        """

        return CaptureGrant(
            request_id=request.request_id,
            granted=True,
            effective_profile=request.profile,
            reason="skeleton_granted",
        )

    def cancel_capture_request(self, request_id: str) -> None:
        """取消采集请求。

        参数：
        - request_id：采集请求标识。
        """
