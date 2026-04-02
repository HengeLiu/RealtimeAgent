"""寻找物体引导逻辑测试。"""

from nextgen.apps.phone.skills.object_detection_skill import ObjectDetectionSkill
from nextgen.apps.phone.tasks.find_object_task import FindObjectTask


def test_detect_from_analysis_marks_left_guidance() -> None:
    """验证目标在手左侧时会给出向左引导。"""

    skill = ObjectDetectionSkill()
    result = skill.detect_from_analysis(
        session_id="tasksess_guidance_001",
        target_name="水杯",
        found=True,
        score=0.93,
        object_center=(100.0, 120.0),
        frame_size=(320, 240),
        hand_center=(200.0, 120.0),
    )

    assert result.found is True
    assert result.extra["guidance_direction"] == "向左"
    assert result.position == "left"


def test_detect_from_analysis_marks_forward_when_touching() -> None:
    """验证手与目标接触后会给出向前建议。"""

    skill = ObjectDetectionSkill()
    result = skill.detect_from_analysis(
        session_id="tasksess_guidance_002",
        target_name="手机",
        found=True,
        score=0.88,
        object_center=(118.0, 118.0),
        frame_size=(320, 240),
        hand_center=(110.0, 110.0),
        hand_box=(90.0, 90.0, 40.0, 40.0),
        polygon=[(100.0, 100.0), (130.0, 100.0), (130.0, 130.0), (100.0, 130.0)],
    )

    assert result.extra["guidance_direction"] == "向前"
    assert result.extra["contact_ratio"] > 0.1


def test_find_object_task_uses_guidance_direction_in_hint() -> None:
    """验证任务组件会消费检测技能生成的引导方向。"""

    skill = ObjectDetectionSkill()
    task = FindObjectTask(target_name="钥匙")
    result = skill.detect_from_analysis(
        session_id="tasksess_guidance_003",
        target_name="钥匙",
        found=True,
        score=0.91,
        object_center=(260.0, 120.0),
        frame_size=(320, 240),
        hand_center=(180.0, 120.0),
    )

    hint = task.update_from_detection(result)
    assert "向右" in hint.text
    assert task.phase == "guiding"
