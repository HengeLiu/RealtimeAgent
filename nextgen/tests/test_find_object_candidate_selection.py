"""寻找物体候选目标选择测试。"""

from nextgen.apps.phone.skills.object_detection_skill import ObjectDetectionSkill


def test_select_primary_object_observation_prefers_larger_area() -> None:
    """验证候选目标选择优先取面积最大的对象。"""

    skill = ObjectDetectionSkill()
    candidate_small = skill.build_object_observation(
        polygon=[(10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0)],
        score=0.99,
    )
    candidate_large = skill.build_object_observation(
        polygon=[(10.0, 10.0), (50.0, 10.0), (50.0, 50.0), (10.0, 50.0)],
        score=0.70,
    )

    primary = skill.select_primary_object_observation([candidate_small, candidate_large])

    assert primary is candidate_large
    assert primary.area == 1600.0


def test_build_frame_analysis_uses_primary_candidate_and_counts_candidates() -> None:
    """验证可从候选列表构造单帧分析输入。"""

    skill = ObjectDetectionSkill()
    candidate_one = skill.build_object_observation(
        polygon=[(50.0, 60.0), (80.0, 60.0), (80.0, 90.0), (50.0, 90.0)],
        score=0.75,
    )
    candidate_two = skill.build_object_observation(
        polygon=[(120.0, 100.0), (190.0, 100.0), (190.0, 170.0), (120.0, 170.0)],
        score=0.85,
    )

    analysis = skill.build_frame_analysis(
        frame_width=320,
        frame_height=240,
        target_name="水杯",
        candidates=[candidate_one, candidate_two],
    )

    assert analysis.found is True
    assert analysis.candidate_count == 2
    assert analysis.object_observation is candidate_two
    assert analysis.target_name == "水杯"
