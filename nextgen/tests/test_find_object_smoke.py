"""寻找物体占位级冒烟测试。"""

from datetime import datetime

from nextgen.apps.phone.skills.object_detection_skill import ObjectDetectionSkill
from nextgen.apps.phone.tasks.find_object_task import FindObjectTask
from nextgen.integration.find_object.scenario import build_find_object_scenario


def test_find_object_placeholder_flow_can_produce_hint() -> None:
    """验证寻找物体占位链路可以从场景走到引导建议。"""

    scenario = build_find_object_scenario()
    task = FindObjectTask(target_name=scenario["target_name"])
    skill = ObjectDetectionSkill()
    result = skill.detect(session_id="tasksess_smoke_001", target_name=scenario["target_name"])
    hint = task.update_from_detection(result)

    assert scenario["task_name"] == "find_object"
    assert result.session_id == "tasksess_smoke_001"
    assert hint.session_id == "tasksess_smoke_001"
    assert hint.text
