from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def fork_yolo_mock(frame: bytes, *, purpose: str, object_name: str = "") -> dict[str, Any]:
    """执行单帧 YOLO mock。

    主要逻辑：保留真实 YOLO 逐帧处理的调用形态，当前只记录日志并返回可预测
    mock 结果，方便后续替换成本地模型推理。
    参数：`frame` 为 JPEG 字节；`purpose` 表示业务场景；`object_name` 是找物目标。
    返回值：包含 mock 识别结果的字典。
    异常情况：当前 mock 不抛业务异常。
    """

    frame_size = len(frame)
    logger.info("yolo.mock.frame_processed purpose=%s object_name=%s frame_size=%s", purpose, object_name, frame_size)
    if purpose == "traffic_light":
        return {
            "type": "traffic_light",
            "state": "green",
            "can_cross": True,
            "confidence": 0.81,
            "source": "mock",
        }
    label = object_name or "目标物"
    return {
        "type": "find_object",
        "label": label,
        "object_name": label,
        "found": True,
        "confidence": 0.76,
        "source": "mock",
    }


def build_mock_result(*, purpose: str, object_name: str, frame_count: int, last_detection: dict[str, Any] | None = None) -> dict[str, Any]:
    """生成端侧视频任务最终 mock 结果。

    主要逻辑：根据业务 purpose 生成 find_object 或 traffic_light 结果，并附带帧数、
    source 和可播报 message。
    参数：`purpose` 为业务场景，`object_name` 为找物目标，`frame_count` 为处理帧数。
    返回值：端侧 command.completed.result。
    异常情况：无。
    """

    detection = dict(last_detection or {})
    if purpose == "traffic_light":
        state = str(detection.get("state") or "green")
        can_cross = bool(detection.get("can_cross", state == "green"))
        message = "绿灯，可以在确认安全后通行" if can_cross else "当前不建议通行，请等待"
        return {
            "type": "traffic_light",
            "state": state,
            "can_cross": can_cross,
            "message": message,
            "frame_count": frame_count,
            "source": "mock",
        }
    label = str(detection.get("object_name") or detection.get("label") or object_name or "目标物")
    confidence = float(detection.get("confidence") or 0.76)
    return {
        "type": "find_object",
        "object_name": label,
        "found": True,
        "confidence": confidence,
        "message": f"已找到{label}，它在前方",
        "frame_count": frame_count,
        "source": "mock",
    }
