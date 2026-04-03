"""原始图片/视频帧接入找物检测链路的测试。"""

from __future__ import annotations

import base64

import cv2
import numpy as np

from nextgen.apps.glass.runtime.app import GlassRuntimeApp
from nextgen.apps.phone.runtime.app import PhoneRuntimeApp
from nextgen.apps.phone.skills.object_detection_skill import ObjectDetectionSkill


def _build_phone_like_frame(width: int = 320, height: int = 240) -> np.ndarray:
    """构造一个带有显著矩形目标的测试画面。"""

    frame = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.rectangle(frame, (210, 50), (285, 190), (255, 255, 255), thickness=-1)
    return frame


def test_object_detection_skill_can_build_analysis_from_raw_image() -> None:
    """验证目标检测技能可以从原始图像帧构造单帧分析。"""

    skill = ObjectDetectionSkill()
    frame = _build_phone_like_frame()
    analysis = skill.build_frame_analysis_from_image(frame=frame, target_name="手机")
    assert analysis.found is True
    assert analysis.candidate_count >= 1
    assert analysis.object_observation is not None
    assert analysis.object_observation.position in {"right", "down_right", "up_right", "center"}


def test_phone_runtime_can_process_stream_frame_message() -> None:
    """验证手机运行时可以把原始帧消息接入找物链路。"""

    runtime = PhoneRuntimeApp()
    runtime.start()
    frame = _build_phone_like_frame()
    encoded_ok, encoded = cv2.imencode(".jpg", frame)
    assert encoded_ok is True
    response = runtime.handle_find_object_stream_frame_message(
        "tasksess_stream_001",
        {
            "task_session_id": "tasksess_stream_001",
            "frame_index": 0,
            "width": frame.shape[1],
            "height": frame.shape[0],
            "jpeg_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
            "target_name": "手机",
            "mark_completed": True,
        },
    )
    assert response["task_session_id"] == "tasksess_stream_001"
    assert response["hint"]["text"].startswith("已发现手机")
    assert response["status"] == "completed"


def test_glass_runtime_executes_hint_response_with_deduping() -> None:
    """验证眼镜运行时会执行找物引导并对重复提示去重。"""

    runtime = GlassRuntimeApp()
    runtime.start()
    runtime.enable_local_speaker()

    spoken = []

    def _fake_execute_speech(text: str):
        spoken.append(text)
        return {
            "speaker_backend": "fake",
            "text": text,
            "status": "spoken",
        }

    runtime.device_control.execute_speech = _fake_execute_speech
    response = {
        "hint": {"text": "已发现手机，位置：right"},
        "state_summary": {"phase": "guiding"},
    }
    hint, first_feedback = runtime._execute_find_object_hint_response("tasksess_stream_001", response)
    _hint_again, second_feedback = runtime._execute_find_object_hint_response("tasksess_stream_001", response)

    assert hint["text"].startswith("已发现手机")
    assert first_feedback["status"] in {"running", "spoken"}
    assert second_feedback["status"] == "deduped"
    assert spoken == ["已发现手机，位置：right"]
