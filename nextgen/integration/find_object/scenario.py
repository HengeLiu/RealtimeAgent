"""寻找物体集成场景。"""

from nextgen.apps.phone.skills.object_detection_skill import ObjectDetectionSkill


def build_find_object_scenario() -> dict:
    """构造寻找物体场景数据。"""

    skill = ObjectDetectionSkill()
    object_observation = skill.build_object_observation(
        polygon=[(220.0, 100.0), (280.0, 100.0), (280.0, 160.0), (220.0, 160.0)],
        score=0.92,
    )
    return {
        "task_name": "find_object",
        "target_name": "手机",
        "voice_text": "帮我找一下手机",
        "candidates": [object_observation],
        "status": "ready",
    }
