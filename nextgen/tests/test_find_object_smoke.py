"""寻找物体占位级冒烟测试。"""

from nextgen.apps.phone.skills.object_detection_skill import ObjectDetectionSkill
from nextgen.apps.phone.tasks.find_object_task import FindObjectTask
from nextgen.integration.find_object.scenario import build_find_object_scenario


def test_find_object_placeholder_flow_can_produce_hint() -> None:
    """验证寻找物体链路可以从检测结果走到引导建议。"""

    scenario = build_find_object_scenario()
    task = FindObjectTask(target_name=scenario["target_name"])
    skill = ObjectDetectionSkill()
    result = skill.detect_from_analysis(
        session_id="tasksess_smoke_001",
        target_name=scenario["target_name"],
        found=True,
        score=0.95,
        object_center=(250.0, 120.0),
        frame_size=(320, 240),
        hand_center=(160.0, 120.0),
    )
    hint = task.update_from_detection(result)

    assert scenario["task_name"] == "find_object"
    assert result.session_id == "tasksess_smoke_001"
    assert result.extra["guidance_direction"] == "向右"
    assert hint.session_id == "tasksess_smoke_001"
    assert hint.text
