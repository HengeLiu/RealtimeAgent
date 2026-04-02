"""寻找物体单帧分析输入测试。"""

from nextgen.apps.phone.skills.object_detection_skill import ObjectDetectionSkill
from nextgen.apps.phone.tasks.find_object_task import FindObjectTask
from nextgen.shared.models import FindObjectFrameAnalysis


def test_detect_from_frame_analysis_uses_structured_input() -> None:
    """验证结构化单帧输入可以直接生成检测结果。"""

    skill = ObjectDetectionSkill()
    object_observation = skill.build_object_observation(
        polygon=[(220.0, 100.0), (260.0, 100.0), (260.0, 140.0), (220.0, 140.0)],
        score=0.95,
    )
    hand_observation = skill.build_hand_observation(
        landmarks=[
            (0.40, 0.40), (0.41, 0.38), (0.42, 0.36), (0.43, 0.34), (0.44, 0.33),
            (0.45, 0.35), (0.46, 0.33), (0.47, 0.31), (0.48, 0.29), (0.50, 0.36),
            (0.51, 0.34), (0.52, 0.32), (0.53, 0.30), (0.54, 0.37), (0.55, 0.35),
            (0.56, 0.33), (0.57, 0.31), (0.58, 0.38), (0.59, 0.36), (0.60, 0.34),
            (0.61, 0.32),
        ],
        frame_width=320,
        frame_height=240,
    )
    analysis = FindObjectFrameAnalysis(
        frame_width=320,
        frame_height=240,
        target_name="背包",
        found=True,
        object_observation=object_observation,
        hand_observation=hand_observation,
        candidate_count=2,
    )

    result = skill.detect_from_frame_analysis("tasksess_frame_001", analysis)

    assert result.found is True
    assert result.extra["candidate_count"] == 2
    assert result.extra["source"] == "legacy_yolomedia"
    assert result.extra["guidance_direction"] in {"向右", "向下", "向前", "保持"}


def test_find_object_task_mentions_grasp_when_present() -> None:
    """验证任务组件会在提示中保留抓握状态。"""

    skill = ObjectDetectionSkill()
    task = FindObjectTask(target_name="背包")
    object_observation = skill.build_object_observation(
        polygon=[(100.0, 100.0), (130.0, 100.0), (130.0, 130.0), (100.0, 130.0)],
        score=0.9,
    )
    hand_observation = skill.build_hand_observation(
        landmarks=[
            (0.40, 0.40), (0.41, 0.39), (0.42, 0.38), (0.43, 0.37), (0.44, 0.45),
            (0.45, 0.35), (0.46, 0.34), (0.47, 0.33), (0.45, 0.45), (0.50, 0.36),
            (0.50, 0.35), (0.50, 0.34), (0.48, 0.44), (0.54, 0.37), (0.55, 0.36),
            (0.56, 0.35), (0.50, 0.43), (0.58, 0.38), (0.59, 0.37), (0.60, 0.36),
            (0.52, 0.42),
        ],
        frame_width=200,
        frame_height=100,
    )
    analysis = FindObjectFrameAnalysis(
        frame_width=200,
        frame_height=100,
        target_name="背包",
        found=True,
        object_observation=object_observation,
        hand_observation=hand_observation,
        candidate_count=1,
    )

    result = skill.detect_from_frame_analysis("tasksess_frame_002", analysis)
    hint = task.update_from_detection(result)

    assert "已检测到抓握动作" in hint.text or result.extra["grasp_detected"] is False
