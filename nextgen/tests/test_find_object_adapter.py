"""寻找物体输入适配逻辑测试。"""

from nextgen.apps.phone.skills.object_detection_skill import ObjectDetectionSkill


def test_build_object_observation_from_polygon() -> None:
    """验证可从目标多边形构造统一物体观测对象。"""

    skill = ObjectDetectionSkill()
    observation = skill.build_object_observation(
        polygon=[(10.0, 10.0), (30.0, 10.0), (30.0, 30.0), (10.0, 30.0)],
        score=0.92,
    )

    assert observation is not None
    assert observation.center_x == 20.0
    assert observation.center_y == 20.0
    assert observation.area == 400.0
    assert observation.score == 0.92


def test_build_hand_observation_from_landmarks() -> None:
    """验证可从手部关键点构造统一手部观测对象。"""

    skill = ObjectDetectionSkill()
    landmarks = [
        (0.40, 0.40), (0.41, 0.38), (0.42, 0.36), (0.43, 0.34), (0.46, 0.45),
        (0.45, 0.35), (0.46, 0.33), (0.47, 0.31), (0.47, 0.45), (0.50, 0.36),
        (0.50, 0.34), (0.50, 0.32), (0.50, 0.43), (0.54, 0.37), (0.55, 0.35),
        (0.56, 0.33), (0.52, 0.42), (0.58, 0.38), (0.59, 0.36), (0.60, 0.34),
        (0.54, 0.41),
    ]

    observation = skill.build_hand_observation(
        landmarks=landmarks,
        frame_width=200,
        frame_height=100,
    )

    assert observation is not None
    assert observation.area > 0.0
    assert observation.bbox.x1 < observation.bbox.x2
    assert observation.bbox.y1 < observation.bbox.y2
    assert isinstance(observation.grasp_detected, bool)
    assert observation.grasp_score >= 0.0
