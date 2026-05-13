from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from .config import TrafficLightVisionConfig
from .models import load_ultralytics_model
from .result import VisionFrameResult, traffic_light_message

logger = logging.getLogger(__name__)

FILTERED_CLASSES = {"crossing", "blank", "countdown_blank"}
RAW_TO_STATE = {
    "stop": "red",
    "countdown_stop": "red",
    "go": "green",
    "countdown_go": "yellow",
}


@dataclass
class TrafficLightDetector:
    """红绿灯 YOLO 检测器。

    主要功能：迁移旧 `trafficlight_detection.py` 的类别过滤和多数表决逻辑，
    输出 red/yellow/green/unknown 与是否可通行。
    """

    config: TrafficLightVisionConfig
    device: str = "auto"
    history: list[str | None] = field(default_factory=list)
    best_known: dict[str, Any] | None = None
    _model: Any | None = None

    def prepare(self) -> None:
        """加载红绿灯模型并重置状态。"""

        self._model = load_ultralytics_model(kind="yolo", model_path=self.config.model_path, device=self.device)
        self.history.clear()
        self.best_known = None
        logger.info("vision.traffic_light.classes names=%s", getattr(self._model, "names", {}))

    def process(self, frame_bgr: np.ndarray, *, frame_count: int) -> VisionFrameResult:
        """处理一帧红绿灯图像。"""

        if self._model is None:
            self.prepare()
        start = time.perf_counter()
        detection = self._detect(frame_bgr)
        self.history.append(detection.get("raw_class"))
        if len(self.history) > self.config.history_size:
            self.history = self.history[-self.config.history_size :]
        stable_raw = self._stable_raw_class()
        if stable_raw:
            detection["stable"] = True
            detection["raw_class"] = stable_raw
            detection["state"] = RAW_TO_STATE.get(stable_raw, "unknown")
            detection["can_cross"] = detection["state"] == "green"
            self.best_known = dict(detection)
            logger.info("vision.traffic_light.stable raw_class=%s state=%s frame_count=%s", stable_raw, detection["state"], frame_count)
        else:
            detection["stable"] = False
        detection["history"] = list(self.history)
        detection["frame_count"] = frame_count
        detection["message"] = traffic_light_message(str(detection.get("state") or "unknown"), bool(detection.get("can_cross", False)))
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "vision.traffic_light.detected raw_class=%s state=%s stable=%s confidence=%.3f elapsed_ms=%s",
            detection.get("raw_class"),
            detection.get("state"),
            detection.get("stable"),
            float(detection.get("confidence") or 0.0),
            elapsed_ms,
        )
        return VisionFrameResult(
            detection=detection,
            should_complete=bool(detection.get("stable")) and bool(detection.get("can_cross")),
            metrics={"inference_ms": elapsed_ms, "history_size": len(self.history)},
            annotated_image=_annotate_traffic_light(frame_bgr, detection),
        )

    def build_final_result(self, *, frame_count: int, last_detection: dict[str, Any] | None) -> dict[str, Any]:
        """生成红绿灯最终结果。"""

        detection = dict(self.best_known or last_detection or {})
        state = str(detection.get("state") or "unknown")
        can_cross = bool(detection.get("can_cross", state == "green"))
        return {
            "type": "traffic_light",
            "state": state,
            "raw_class": detection.get("raw_class"),
            "can_cross": can_cross,
            "confidence": float(detection.get("confidence") or 0.0),
            "message": traffic_light_message(state, can_cross),
            "frame_count": frame_count,
            "source": detection.get("source") or "yolo",
        }

    def _detect(self, frame_bgr: np.ndarray) -> dict[str, Any]:
        model = self._model
        if model is None:
            return _unknown("model_not_ready")
        results = model(frame_bgr, conf=self.config.conf, verbose=False)
        best = _best_traffic_box(results[0] if results else None, getattr(model, "names", {}))
        if best is None and self.config.enable_hsv_fallback:
            return _hsv_fallback(frame_bgr)
        if best is None:
            return _unknown("no_detection")
        raw_class = best["raw_class"]
        state = RAW_TO_STATE.get(raw_class, "unknown")
        return {
            "type": "traffic_light",
            "raw_class": raw_class,
            "state": state,
            "can_cross": state == "green",
            "confidence": float(best["confidence"]),
            "bbox": best["bbox"],
            "source": "yolo",
        }

    def _stable_raw_class(self) -> str | None:
        valid = [item for item in self.history if item in RAW_TO_STATE]
        if len(valid) < self.config.majority_threshold:
            return None
        raw, count = Counter(valid).most_common(1)[0]
        if count >= self.config.majority_threshold:
            return str(raw)
        return None


def _best_traffic_box(result: Any, names: dict[int, str] | dict[Any, Any]) -> dict[str, Any] | None:
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) <= 0:
        return None
    best: dict[str, Any] | None = None
    for box in boxes:
        cls_id = int(box.cls[0])
        confidence = float(box.conf[0])
        raw_class = str(names.get(cls_id, f"class_{cls_id}")).lower()
        if raw_class in FILTERED_CLASSES:
            continue
        if raw_class not in RAW_TO_STATE:
            continue
        bbox = [int(v) for v in box.xyxy[0]]
        if best is None or confidence > float(best["confidence"]):
            best = {"raw_class": raw_class, "confidence": confidence, "bbox": bbox}
    return best


def _hsv_fallback(frame_bgr: np.ndarray) -> dict[str, Any]:
    h, _w = frame_bgr.shape[:2]
    roi = frame_bgr[: int(h * 0.5), :]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    masks = {
        "stop": cv2.bitwise_or(
            cv2.inRange(hsv, np.array([0, 80, 120]), np.array([10, 255, 255])),
            cv2.inRange(hsv, np.array([160, 80, 120]), np.array([180, 255, 255])),
        ),
        "go": cv2.inRange(hsv, np.array([40, 60, 120]), np.array([90, 255, 255])),
        "countdown_go": cv2.inRange(hsv, np.array([18, 80, 150]), np.array([35, 255, 255])),
    }
    total = max(1, roi.shape[0] * roi.shape[1])
    scores = {name: float(np.count_nonzero(mask)) / total for name, mask in masks.items()}
    raw_class, score = max(scores.items(), key=lambda item: item[1])
    if score < 0.003:
        return _unknown("hsv_no_color")
    state = RAW_TO_STATE.get(raw_class, "unknown")
    return {
        "type": "traffic_light",
        "raw_class": raw_class,
        "state": state,
        "can_cross": state == "green",
        "confidence": min(0.5, score * 20),
        "source": "hsv_fallback",
    }


def _unknown(reason: str) -> dict[str, Any]:
    return {
        "type": "traffic_light",
        "raw_class": None,
        "state": "unknown",
        "can_cross": False,
        "confidence": 0.0,
        "reason": reason,
        "source": "yolo",
    }


def _annotate_traffic_light(frame_bgr: np.ndarray, detection: dict[str, Any]) -> np.ndarray:
    annotated = frame_bgr.copy()
    bbox = detection.get("bbox")
    if bbox:
        color = (0, 255, 0) if detection.get("state") == "green" else (0, 0, 255) if detection.get("state") == "red" else (0, 255, 255)
        x1, y1, x2, y2 = [int(v) for v in bbox]
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        cv2.putText(annotated, str(detection.get("state") or "unknown"), (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    return annotated
