"""共享模型测试。"""

from nextgen.shared.enums.common import CaptureMode, ExecutorMode, RuntimeType, SensorType, TaskCategory, TaskPriority, TaskStatus
from nextgen.shared.models import (
    CaptureProfile,
    CaptureRequest,
    DeviceInfo,
    NetworkStatus,
    Resolution,
    SourceTargetRef,
    TaskDefinition,
    TaskSession,
)


def test_task_session_to_dict_contains_expected_fields() -> None:
    """验证任务实例模型可以稳定输出关键字段。"""

    session = TaskSession(
        session_id="tasksess_test_001",
        task_name="find_object",
        status=TaskStatus.RUNNING,
        phase="guiding",
        priority=TaskPriority.HIGH,
        created_at="2026-04-02T12:00:00+08:00",
        updated_at="2026-04-02T12:00:01+08:00",
        initiator=SourceTargetRef(runtime=RuntimeType.SERVER, device_id="server-main"),
    )
    data = session.to_dict()
    assert data["task_name"] == "find_object"
    assert data["status"] == "running"
    assert data["phase"] == "guiding"


def test_capture_request_to_dict_contains_profile() -> None:
    """验证采集请求模型包含嵌套采集参数。"""

    request = CaptureRequest(
        request_id="capreq_test_001",
        session_id="tasksess_test_001",
        sensor=SensorType.RGB_CAMERA,
        mode=CaptureMode.STREAM,
        priority=TaskPriority.HIGH,
        profile=CaptureProfile(
            fps=5,
            resolution=Resolution(width=640, height=480),
            quality="medium",
        ),
        consumer=SourceTargetRef(runtime=RuntimeType.PHONE, device_id="phone-001"),
    )
    data = request.to_dict()
    assert data["sensor"] == "rgb_camera"
    assert data["profile"]["fps"] == 5
    assert data["profile"]["resolution"]["width"] == 640


def test_device_info_to_dict_contains_capabilities() -> None:
    """验证设备模型可以输出能力列表。"""

    device = DeviceInfo(
        device_id="glass-001",
        runtime=RuntimeType.GLASS,
        display_name="主眼镜",
        online=True,
        network=NetworkStatus(control_connected=True, peer_connected=False),
    )
    data = device.to_dict()
    assert data["device_id"] == "glass-001"
    assert data["runtime"] == "glass"


def test_task_definition_to_dict_contains_executor_mode() -> None:
    """验证任务模板模型可以输出执行模式。"""

    definition = TaskDefinition(
        task_name="find_object",
        task_category=TaskCategory.BACKGROUND,
        description="寻找物体",
        executor_mode=ExecutorMode.HYBRID,
        participants=["glass", "phone", "server"],
    )
    data = definition.to_dict()
    assert data["task_name"] == "find_object"
