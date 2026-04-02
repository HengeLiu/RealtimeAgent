"""感知总线测试。"""

from nextgen.apps.glass.sensors.sensor_hub import GlassSensorHub
from nextgen.shared.enums.common import CaptureMode, RuntimeType, SensorType, TaskPriority
from nextgen.shared.models import CaptureProfile, CaptureRequest, Resolution, SourceTargetRef


def test_sensor_hub_merges_capture_requests_by_sensor() -> None:
    """验证感知总线会合并同一采集器的多个请求。"""

    hub = GlassSensorHub()
    request_one = CaptureRequest(
        request_id="capreq_001",
        session_id="tasksess_001",
        sensor=SensorType.RGB_CAMERA,
        mode=CaptureMode.STREAM,
        priority=TaskPriority.NORMAL,
        profile=CaptureProfile(
            fps=5,
            resolution=Resolution(width=640, height=480),
            quality="medium",
        ),
        consumer=SourceTargetRef(runtime=RuntimeType.PHONE, device_id="phone-001"),
    )
    request_two = CaptureRequest(
        request_id="capreq_002",
        session_id="tasksess_002",
        sensor=SensorType.RGB_CAMERA,
        mode=CaptureMode.SNAPSHOT,
        priority=TaskPriority.HIGH,
        profile=CaptureProfile(
            fps=10,
            resolution=Resolution(width=1280, height=720),
            quality="high",
        ),
        consumer=SourceTargetRef(runtime=RuntimeType.SERVER, device_id="server-main"),
    )

    hub.register_capture_request(request_one)
    grant = hub.register_capture_request(request_two)

    assert grant.granted is True
    assert grant.effective_profile.fps == 10
    assert grant.effective_profile.resolution.width == 1280
    assert grant.effective_profile.quality == "high"


def test_sensor_hub_cancel_request_updates_active_list() -> None:
    """验证取消采集请求后会释放活跃请求。"""

    hub = GlassSensorHub()
    request = CaptureRequest(
        request_id="capreq_003",
        session_id="tasksess_003",
        sensor=SensorType.MICROPHONE,
        mode=CaptureMode.CONTINUOUS_STATE,
        priority=TaskPriority.NORMAL,
        profile=CaptureProfile(fps=50),
        consumer=SourceTargetRef(runtime=RuntimeType.GLASS, device_id="glass-001"),
    )

    hub.register_capture_request(request)
    hub.cancel_capture_request("capreq_003")

    assert hub.list_active_requests(sensor=SensorType.MICROPHONE.value) == []
