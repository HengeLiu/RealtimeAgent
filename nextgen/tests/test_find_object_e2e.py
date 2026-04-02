"""寻找物体端到端集成测试。"""

from nextgen.apps.phone.skills.object_detection_skill import ObjectDetectionSkill
from nextgen.integration.find_object.runner import FindObjectIntegrationRunner
from nextgen.integration.find_object.scenario import build_find_object_scenario
from nextgen.shared.enums.common import CaptureMode, RuntimeType, SensorType, TaskPriority
from nextgen.shared.models import CaptureProfile, CaptureRequest, Resolution, SourceTargetRef


def test_find_object_e2e_happy_path_completes_task() -> None:
    """验证寻找物体 happy path 可以完整完成。"""

    scenario = build_find_object_scenario()
    runner = FindObjectIntegrationRunner()
    runner.start()
    try:
        result = runner.run_find_object(
            voice_text=scenario["voice_text"],
            target_name=scenario["target_name"],
            candidates=scenario["candidates"],
            mark_completed=True,
        )
    finally:
        runner.stop()

    assert result.task_name == "find_object"
    assert result.final_status == "completed"
    assert result.final_phase == "completed"
    assert "已发现手机" in result.phone_hint["text"]
    assert result.execution_feedback["status"] == "running"


def test_find_object_e2e_can_keep_scanning_when_target_missing() -> None:
    """验证未找到目标时会继续扫描而不是直接完成。"""

    runner = FindObjectIntegrationRunner()
    runner.start()
    try:
        result = runner.run_find_object(
            voice_text="帮我找一下钱包",
            target_name="钱包",
            candidates=[],
            mark_completed=False,
        )
    finally:
        runner.stop()

    assert result.final_status == "running"
    assert result.final_phase == "guiding"
    assert "继续扫描" in result.phone_hint["text"]


def test_find_object_e2e_keeps_server_status_logs() -> None:
    """验证服务器会记录完整任务状态日志。"""

    scenario = build_find_object_scenario()
    runner = FindObjectIntegrationRunner()
    runner.start()
    try:
        result = runner.run_find_object(
            voice_text=scenario["voice_text"],
            target_name=scenario["target_name"],
            candidates=scenario["candidates"],
            mark_completed=True,
        )
    finally:
        runner.stop()

    event_names = [record["event"]["event_name"] for record in result.server_recent_logs if record.get("record_type") == "task_event"]
    assert "hybrid_task_created" in event_names
    assert "capture_request_granted" in event_names
    assert "phone_hint_generated" in event_names
    assert "find_object_completed" in event_names


def test_find_object_e2e_releases_capture_request_after_completion() -> None:
    """验证任务完成后采集请求会被释放。"""

    scenario = build_find_object_scenario()
    runner = FindObjectIntegrationRunner()
    runner.start()
    try:
        result = runner.run_find_object(
            voice_text=scenario["voice_text"],
            target_name=scenario["target_name"],
            candidates=scenario["candidates"],
            mark_completed=True,
        )
        active_requests = runner.glass.sensor_hub.list_active_requests(sensor="rgb_camera")
    finally:
        runner.stop()

    assert result.final_status == "completed"
    assert active_requests == []


def test_find_object_e2e_can_handle_competing_capture_requests() -> None:
    """验证找物任务运行期间仍可处理竞争采集请求。"""

    skill = ObjectDetectionSkill()
    candidate = skill.build_object_observation(
        polygon=[(230.0, 100.0), (280.0, 100.0), (280.0, 150.0), (230.0, 150.0)],
        score=0.89,
    )
    runner = FindObjectIntegrationRunner()
    runner.start()
    try:
        result = runner.run_find_object(
            voice_text="帮我找一下门卡",
            target_name="门卡",
            candidates=[candidate],
            mark_completed=False,
        )
        followup_request = CaptureRequest(
            request_id="capreq_followup",
            session_id=result.session_id,
            sensor=SensorType.RGB_CAMERA,
            mode=CaptureMode.SNAPSHOT,
            priority=TaskPriority.URGENT,
            profile=CaptureProfile(
                fps=10,
                resolution=Resolution(width=1920, height=1080),
                quality="high",
            ),
            consumer=SourceTargetRef(
                runtime=RuntimeType.SERVER,
                device_id="server-main",
            ),
        )
        followup_grant = runner.glass.sensor_hub.register_capture_request(followup_request)
    finally:
        runner.stop()

    assert followup_grant.granted is True
    assert followup_grant.effective_profile.resolution.width == 1920
