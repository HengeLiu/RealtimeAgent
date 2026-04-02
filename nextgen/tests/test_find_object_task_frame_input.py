"""寻找物体任务组件单帧输入测试。"""

from nextgen.apps.phone.skills.object_detection_skill import ObjectDetectionSkill
from nextgen.apps.phone.tasks.find_object_task import FindObjectTask


def test_find_object_task_can_consume_frame_analysis_directly() -> None:
    """验证任务组件可以直接消费单帧分析输入。"""

    skill = ObjectDetectionSkill()
    task = FindObjectTask(target_name="雨伞")
    object_observation = skill.build_object_observation(
        polygon=[(210.0, 100.0), (260.0, 100.0), (260.0, 150.0), (210.0, 150.0)],
        score=0.93,
    )
    analysis = skill.build_frame_analysis(
        frame_width=320,
        frame_height=240,
        target_name="雨伞",
        candidates=[object_observation],
    )

    hint = task.update_from_frame_analysis(
        session_id="tasksess_task_frame_001",
        analysis=analysis,
    )

    assert hint.session_id == "tasksess_task_frame_001"
    assert "已发现雨伞" in hint.text
