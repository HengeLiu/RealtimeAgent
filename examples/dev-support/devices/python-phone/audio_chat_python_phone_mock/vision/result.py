from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VisionFrameResult:
    """单帧视觉处理结果。

    主要功能：在 `PeerVideoReceiver` 和具体 detector 之间传递结构化识别结果，
    同时表达是否可以结束当前远程任务。
    """

    detection: dict[str, Any]
    should_complete: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    annotated_image: Any | None = None


def find_object_message(object_name: str, detection: dict[str, Any]) -> str:
    """生成找物播报文本。

    参数：`object_name` 为目标名称，`detection` 为识别结果。
    返回值：适合 server Task 播报的中文文本。
    异常情况：无。
    """

    if not detection.get("found"):
        return f"暂时没有找到{object_name or '目标物'}"
    center = detection.get("center") if isinstance(detection.get("center"), dict) else {}
    x_ratio = center.get("x_ratio")
    if isinstance(x_ratio, (int, float)):
        if x_ratio < 0.38:
            direction = "画面左侧"
        elif x_ratio > 0.62:
            direction = "画面右侧"
        else:
            direction = "画面中间"
    else:
        direction = "前方"
    return f"已找到{object_name or detection.get('object_name') or '目标物'}，在{direction}"


def traffic_light_message(state: str, can_cross: bool) -> str:
    """生成红绿灯播报文本。

    参数：`state` 为 red/yellow/green/unknown，`can_cross` 表示是否建议通行。
    返回值：适合 server Task 播报的中文文本。
    异常情况：无。
    """

    if can_cross:
        return "绿灯稳定，可以在确认安全后通行"
    if state == "red":
        return "当前是红灯，请等待"
    if state == "yellow":
        return "当前是黄灯，不建议通行"
    return "暂时没有稳定识别到绿灯，请等待"
