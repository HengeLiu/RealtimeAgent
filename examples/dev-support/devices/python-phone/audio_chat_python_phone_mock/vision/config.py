from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_MODEL_ROOT = Path("/Users/elio/.cache/modelscope/hub/models/archifancy/AIGlasses_for_navigation")


@dataclass(frozen=True)
class FindObjectVisionConfig:
    """找物视觉配置。

    主要功能：保存 YOLOE 找物模型路径、阈值和稳定命中策略。
    """

    backend: str = "yoloe"
    model_path: str = str(DEFAULT_MODEL_ROOT / "yoloe-11l-seg.pt")
    conf: float = 0.20
    iou: float = 0.45
    imgsz: int = 640
    stable_hits: int = 2
    min_area_ratio: float = 0.001


@dataclass(frozen=True)
class TrafficLightVisionConfig:
    """红绿灯视觉配置。

    主要功能：保存红绿灯 YOLO 模型路径、类别过滤和多数表决参数。
    """

    backend: str = "yolo"
    model_path: str = str(DEFAULT_MODEL_ROOT / "trafficlight.pt")
    conf: float = 0.25
    history_size: int = 5
    majority_threshold: int = 3
    enable_hsv_fallback: bool = True


@dataclass(frozen=True)
class VisionConfig:
    """Python phone 视觉处理配置。

    主要功能：统一描述 peer video 任务使用 mock 还是真实 YOLO、模型设备、帧率控制
    和两个业务 detector 的配置。
    """

    provider: str = "mock"
    device: str = "auto"
    frame_stride: int = 1
    save_annotated_frame: str = "runs/audio-chat/python-phone/latest-yolo.jpg"
    fallback_to_mock: bool = False
    find_object: FindObjectVisionConfig = field(default_factory=FindObjectVisionConfig)
    traffic_light: TrafficLightVisionConfig = field(default_factory=TrafficLightVisionConfig)

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "VisionConfig":
        """从 YAML 配置字典创建视觉配置。

        参数：`value` 为 `phone.preview.yaml` 中的 `vision` 字段。
        返回值：归一化后的 `VisionConfig`。
        异常情况：数值类型错误时由 Python 转换异常向上抛出。
        """

        raw = dict(value or {})
        find_raw = dict(raw.get("find_object") or {})
        traffic_raw = dict(raw.get("traffic_light") or {})
        return cls(
            provider=str(raw.get("provider") or "mock").strip().lower(),
            device=str(raw.get("device") or "auto").strip().lower(),
            frame_stride=max(1, int(raw.get("frame_stride") or 1)),
            save_annotated_frame=str(raw.get("save_annotated_frame") or "runs/audio-chat/python-phone/latest-yolo.jpg"),
            fallback_to_mock=bool(raw.get("fallback_to_mock", False)),
            find_object=FindObjectVisionConfig(
                backend=str(find_raw.get("backend") or "yoloe"),
                model_path=_expand_path(str(find_raw.get("model_path") or DEFAULT_MODEL_ROOT / "yoloe-11l-seg.pt")),
                conf=float(find_raw.get("conf") or 0.20),
                iou=float(find_raw.get("iou") or 0.45),
                imgsz=int(find_raw.get("imgsz") or 640),
                stable_hits=max(1, int(find_raw.get("stable_hits") or 2)),
                min_area_ratio=float(find_raw.get("min_area_ratio") or 0.001),
            ),
            traffic_light=TrafficLightVisionConfig(
                backend=str(traffic_raw.get("backend") or "yolo"),
                model_path=_expand_path(str(traffic_raw.get("model_path") or DEFAULT_MODEL_ROOT / "trafficlight.pt")),
                conf=float(traffic_raw.get("conf") or 0.25),
                history_size=max(1, int(traffic_raw.get("history_size") or 5)),
                majority_threshold=max(1, int(traffic_raw.get("majority_threshold") or 3)),
                enable_hsv_fallback=bool(traffic_raw.get("enable_hsv_fallback", True)),
            ),
        )


def _expand_path(value: str) -> str:
    """展开配置路径中的环境变量和用户目录。

    参数：`value` 为原始路径，支持 `${VAR}` 和 `~`。
    返回值：展开后的路径字符串。
    异常情况：无。
    """

    return str(Path(os.path.expandvars(value)).expanduser())
