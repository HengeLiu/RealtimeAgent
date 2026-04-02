"""场景测试。"""

from nextgen.apps.glass.runtime.app import GlassRuntimeApp
from nextgen.apps.phone.runtime.app import PhoneRuntimeApp
from nextgen.apps.server.runtime.app import ServerRuntimeApp
from nextgen.shared.enums.common import CaptureMode, RuntimeType, SensorType, TaskPriority
from nextgen.shared.models import CaptureProfile, CaptureRequest, Resolution, SourceTargetRef


def test_scenario_voice_to_find_object_task_creation() -> None:
    """场景一：用户说“帮我找一下手机”，服务器创建找物任务。"""

    glass = GlassRuntimeApp()
    server = ServerRuntimeApp()
    glass.start()
    server.start()
    server.event_router.enable_keyword_dispatch = True

    event = glass.build_voice_event("帮我找一下手机", "audio://001", 0.95)
    route_result = server.event_router.route(event.to_dict())

    assert route_result["dispatch_result"]["task_name"] == "find_object"


def test_scenario_phone_generates_guidance_after_detection() -> None:
    """场景二：手机检测到目标后，直接生成引导建议。"""

    phone = PhoneRuntimeApp()
    phone.start()
    candidate = phone.object_detection_skill.build_object_observation(
        polygon=[(220.0, 110.0), (270.0, 110.0), (270.0, 160.0), (220.0, 160.0)],
        score=0.91,
    )

    hint = phone.analyze_find_object_frame("tasksess_scene_002", "钱包", [candidate])

    assert "已发现钱包" in hint.text


def test_scenario_sensor_hub_handles_competing_camera_requests() -> None:
    """场景三：找物过程中又来了新的拍照请求，感知总线合并参数。"""

    glass = GlassRuntimeApp()
    glass.start()
    stream_request = CaptureRequest(
        request_id="capreq_scene_001",
        session_id="tasksess_scene_003",
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
    snapshot_request = CaptureRequest(
        request_id="capreq_scene_002",
        session_id="tasksess_scene_004",
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

    glass.sensor_hub.register_capture_request(stream_request)
    grant = glass.sensor_hub.register_capture_request(snapshot_request)

    assert grant.effective_profile.resolution.width == 1280


def test_scenario_server_answers_latest_task_status() -> None:
    """场景四：用户询问任务状态，服务器返回最近任务状态。"""

    server = ServerRuntimeApp()
    server.start()
    server.create_hybrid_task.run("find_object", {"target_name": "钥匙"})

    answer = server.agent_center.answer_task_status()

    assert "最近任务" in answer["answer"]
    assert answer["session"]["task_name"] == "find_object"


def test_scenario_glass_executes_phone_guidance() -> None:
    """场景五：手机生成引导建议后，眼镜执行播报请求。"""

    phone = PhoneRuntimeApp()
    glass = GlassRuntimeApp()
    phone.start()
    glass.start()
    candidate = phone.object_detection_skill.build_object_observation(
        polygon=[(240.0, 100.0), (280.0, 100.0), (280.0, 140.0), (240.0, 140.0)],
        score=0.88,
    )
    hint = phone.analyze_find_object_frame("tasksess_scene_005", "门卡", [candidate])

    feedback = glass.executor_bus.submit(
        request=__import__("nextgen.shared.models", fromlist=["ExecutionRequest"]).ExecutionRequest(
            execution_id="exec_scene_005",
            session_id="tasksess_scene_005",
            execution_type=__import__("nextgen.shared.enums.common", fromlist=["ExecutionType"]).ExecutionType.SPEECH,
            priority=TaskPriority.HIGH,
            payload={"text": hint.text},
        )
    )

    assert feedback.status == "running"
    assert "门卡" in hint.text
