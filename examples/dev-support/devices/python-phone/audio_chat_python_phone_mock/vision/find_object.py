from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable

import cv2
import numpy as np

from .config import FindObjectVisionConfig
from .models import VisionDependencyError, load_ultralytics_model
from .result import VisionFrameResult, find_object_message

logger = logging.getLogger(__name__)

_TEXT_MODEL_DOWNLOAD_LOCK = Lock()
_TEXT_MODEL_CACHE_DIR = Path("runs/audio-chat/python-phone/vision-cache")
_TEXT_MODEL_CACHE_PATTERNS = ("mobileclip*", "*.ts.tmp", "*.pt.tmp")


@dataclass
class FindObjectDetector:
    """YOLOE 找物检测器。

    主要功能：按用户给定目标名称设置 YOLOE 文本 prompt，逐帧检测目标 mask/box，
    并在连续稳定命中后给出可完成的找物结果。
    """

    config: FindObjectVisionConfig
    device: str = "auto"
    object_name: str = "目标物"
    stable_hits: int = 0
    last_track_id: int | None = None
    log_callback: Callable[[str, str], Any] | None = None
    _model: Any | None = None
    _prompt: str = ""

    def prepare(self, object_name: str) -> None:
        """准备找物模型和 prompt。

        参数：`object_name` 为本次任务目标。
        返回值：无。
        异常情况：模型缺失、依赖缺失或 prompt 设置失败时向上抛出。
        """

        self.object_name = object_name or "目标物"
        self._emit_log("INFO", f"YOLOE 找物模型加载开始 model={self.config.model_path} device={self.device}")
        self._model = load_ultralytics_model(kind="yoloe", model_path=self.config.model_path, device=self.device)
        self._emit_log("INFO", "YOLOE 找物模型加载完成")
        if self._prompt != self.object_name:
            set_classes = getattr(self._model, "set_classes", None)
            get_text_pe = getattr(self._model, "get_text_pe", None)
            if callable(set_classes) and callable(get_text_pe):
                self._emit_log("INFO", f"YOLOE 文本 prompt 准备开始 object_name={self.object_name}")
                _ensure_yoloe_text_dependency()
                self._model.set_classes([self.object_name], _get_yoloe_text_pe(self._model, [self.object_name], log_callback=self.log_callback))
                self._emit_log("INFO", f"YOLOE 文本 prompt 准备完成 object_name={self.object_name}")
            else:
                self._emit_log("WARNING", "YOLOE 模型缺少 set_classes/get_text_pe，跳过文本 prompt 设置")
            self._prompt = self.object_name
        self.stable_hits = 0
        self.last_track_id = None

    def process(self, frame_bgr: np.ndarray, *, frame_count: int) -> VisionFrameResult:
        """处理一帧图像。

        参数：`frame_bgr` 为 OpenCV BGR 图像，`frame_count` 为 peer video 帧序号。
        返回值：结构化找物结果。
        异常情况：模型推理异常时向上抛出，由 receiver 转为 command.failed。
        """

        if self._model is None:
            self.prepare(self.object_name)
        start = time.perf_counter()
        self._emit_log("DEBUG", f"YOLOE 找物推理开始 frame_count={frame_count}")
        detection = self._detect(frame_bgr)
        if detection.get("found"):
            self.stable_hits += 1
        else:
            self.stable_hits = 0
        detection["stable_hits"] = self.stable_hits
        detection["frame_count"] = frame_count
        detection["message"] = find_object_message(self.object_name, detection)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "vision.find_object.detected object_name=%s found=%s confidence=%.3f stable_hits=%s elapsed_ms=%s",
            self.object_name,
            detection.get("found"),
            float(detection.get("confidence") or 0.0),
            self.stable_hits,
            elapsed_ms,
        )
        self._emit_log(
            "DEBUG",
            (
                f"YOLOE 找物推理完成 frame_count={frame_count} found={detection.get('found')} "
                f"confidence={float(detection.get('confidence') or 0.0):.3f} elapsed_ms={elapsed_ms}"
            ),
        )
        return VisionFrameResult(
            detection=detection,
            should_complete=bool(detection.get("found")) and self.stable_hits >= self.config.stable_hits,
            metrics={"inference_ms": elapsed_ms, "stable_hits": self.stable_hits},
            annotated_image=_annotate_find_object(frame_bgr, detection),
        )

    def build_final_result(self, *, frame_count: int, last_detection: dict[str, Any] | None) -> dict[str, Any]:
        """生成找物最终结果。

        参数：`frame_count` 为总帧数，`last_detection` 为最近一次识别结果。
        返回值：用于 `command.completed.result` 的 payload。
        异常情况：无。
        """

        detection = dict(last_detection or {})
        found = bool(detection.get("found", False))
        detection.update(
            {
                "type": "find_object",
                "object_name": self.object_name,
                "found": found,
                "frame_count": frame_count,
                "source": detection.get("source") or "yoloe",
                "message": find_object_message(self.object_name, detection),
            }
        )
        if "confidence" not in detection:
            detection["confidence"] = 0.0
        return detection

    def _emit_log(self, level: str, message: str) -> None:
        """输出找物检测器日志，并同步到可选 GUI 日志面板。"""

        _emit_callback_log(self.log_callback, level, message)

    def _detect(self, frame_bgr: np.ndarray) -> dict[str, Any]:
        model = self._model
        if model is None:
            return _not_found(self.object_name, "model_not_ready")
        result = _run_segment(model, frame_bgr, self.config)
        masks = result["masks"]
        if not masks:
            return _not_found(self.object_name, "no_detection")
        chosen = _choose_detection(result, last_track_id=self.last_track_id)
        if chosen is None:
            return _not_found(self.object_name, "no_valid_detection")
        h, w = frame_bgr.shape[:2]
        mask = chosen["mask"]
        area = int(np.count_nonzero(mask))
        area_ratio = area / float(max(1, h * w))
        if area_ratio < self.config.min_area_ratio:
            return _not_found(self.object_name, "area_too_small")
        bbox = chosen.get("bbox") or _mask_bbox(mask)
        center = _bbox_center(bbox, width=w, height=h)
        if chosen.get("track_id") is not None:
            self.last_track_id = int(chosen["track_id"])
        return {
            "type": "find_object",
            "object_name": self.object_name,
            "label": self.object_name,
            "found": True,
            "confidence": float(chosen.get("confidence") or 0.0),
            "bbox": [int(v) for v in bbox],
            "center": center,
            "area_ratio": area_ratio,
            "direction_hint": _direction_hint(center),
            "source": "yoloe",
        }


def _run_segment(model: Any, frame_bgr: np.ndarray, config: FindObjectVisionConfig) -> dict[str, list[Any]]:
    """运行 YOLOE 分割或检测。

    参数：`model` 为 ultralytics 模型，`frame_bgr` 为输入图像，`config` 为阈值配置。
    返回值：归一化的 masks/boxes/confidences/ids。
    异常情况：模型推理异常时向上抛出。
    """

    # 找物任务只需要逐帧检测结果，跨帧稳定性由 stable_hits 处理。
    # Ultralytics 的 track() 会额外导入 lap 等 tracker 依赖，端侧没有必要承担这层依赖。
    predict = getattr(model, "predict", None)
    if callable(predict):
        raw = predict(frame_bgr, conf=config.conf, iou=config.iou, imgsz=config.imgsz, verbose=False)
    else:
        raw = model(frame_bgr, conf=config.conf, iou=config.iou, imgsz=config.imgsz, verbose=False)
    if not raw:
        return {"masks": [], "boxes": [], "confidences": [], "ids": []}
    r0 = raw[0]
    h, w = frame_bgr.shape[:2]
    boxes_obj = getattr(r0, "boxes", None)
    masks_obj = getattr(r0, "masks", None)
    boxes = []
    confidences = []
    ids = []
    if boxes_obj is not None and getattr(boxes_obj, "xyxy", None) is not None:
        xyxy = boxes_obj.xyxy.detach().cpu().numpy()
        conf = boxes_obj.conf.detach().cpu().numpy() if getattr(boxes_obj, "conf", None) is not None else [0.0] * len(xyxy)
        track_ids = boxes_obj.id.int().detach().cpu().tolist() if getattr(boxes_obj, "id", None) is not None else [None] * len(xyxy)
        for idx, item in enumerate(xyxy):
            boxes.append(tuple(float(v) for v in item))
            confidences.append(float(conf[idx]))
            ids.append(track_ids[idx])
    masks = []
    if masks_obj is not None and getattr(masks_obj, "data", None) is not None:
        for mask_t in masks_obj.data:
            mask = mask_t.detach().cpu().numpy()
            if mask.shape[:2] != (h, w):
                mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
            masks.append((mask > 0.5).astype(np.uint8))
    if not masks:
        for box in boxes:
            x1, y1, x2, y2 = [int(v) for v in box]
            mask = np.zeros((h, w), dtype=np.uint8)
            mask[max(0, y1) : min(h, y2), max(0, x1) : min(w, x2)] = 1
            masks.append(mask)
    return {"masks": masks, "boxes": boxes, "confidences": confidences, "ids": ids}


def _choose_detection(result: dict[str, list[Any]], *, last_track_id: int | None) -> dict[str, Any] | None:
    masks = result["masks"]
    if not masks:
        return None
    ids = result.get("ids") or []
    boxes = result.get("boxes") or []
    confidences = result.get("confidences") or []
    chosen_idx = None
    if last_track_id is not None and last_track_id in ids:
        chosen_idx = ids.index(last_track_id)
    if chosen_idx is None:
        chosen_idx = int(np.argmax([int(np.count_nonzero(mask)) for mask in masks]))
    return {
        "mask": masks[chosen_idx],
        "bbox": boxes[chosen_idx] if chosen_idx < len(boxes) else None,
        "confidence": confidences[chosen_idx] if chosen_idx < len(confidences) else 0.0,
        "track_id": ids[chosen_idx] if chosen_idx < len(ids) else None,
    }


def _ensure_yoloe_text_dependency() -> None:
    """确认 YOLOE 文本提示依赖已经安装。

    主要逻辑：YOLOE 在 `get_text_pe()` 中会导入 `clip`。如果缺少该依赖，
    Ultralytics 会尝试用当前解释器执行 `python -m pip install`，这在 uv 创建的
    端侧虚拟环境中经常没有 pip，错误也不够清晰。这里提前检查并给出 phone 端
    自己的安装命令。
    返回值：无。
    异常情况：缺少 `clip` 时抛出 `VisionDependencyError`。
    """

    try:
        import clip  # type: ignore  # noqa: F401
    except ModuleNotFoundError as exc:
        raise VisionDependencyError(
            "缺少 YOLOE 文本提示依赖 clip，请在 Python phone 端执行: "
            "uv pip install -r examples/dev-support/devices/python-phone/requirements.vision.txt"
        ) from exc


def _get_yoloe_text_pe(model: Any, texts: list[str], *, log_callback: Callable[[str, str], Any] | None = None) -> Any:
    """在端侧缓存目录中生成 YOLOE 文本特征。

    主要逻辑：Ultralytics 会在 `get_text_pe()` 内按相对路径下载
    `mobileclip_blt.ts`。这里把下载过程限制到 Python phone 的运行产物目录，
    避免大模型权重落到仓库根目录。由于 `os.chdir()` 是进程级状态，使用锁避免
    多个找物任务并发准备模型时互相影响。
    参数：`model` 为 YOLOE 模型，`texts` 为文本 prompt 列表。
    返回值：YOLOE 文本特征张量。
    异常情况：下载、加载或编码失败时向上抛出，由 receiver 转为 `command.failed`。
    """

    cwd = Path.cwd()
    cache_dir = (cwd / _TEXT_MODEL_CACHE_DIR).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    with _TEXT_MODEL_DOWNLOAD_LOCK:
        os.chdir(cache_dir)
        try:
            for attempt in range(2):
                try:
                    _emit_callback_log(
                        log_callback,
                        "INFO",
                        f"YOLOE 文本编码开始 cache_dir={cache_dir} attempt={attempt + 1}",
                    )
                    return model.get_text_pe(texts)
                except RuntimeError as exc:
                    if attempt == 0 and _is_corrupted_torch_archive_error(exc):
                        removed = _remove_cached_text_model_assets(cache_dir, log_callback=log_callback)
                        _emit_callback_log(
                            log_callback,
                            "WARNING",
                            f"YOLOE 文本编码权重缓存损坏，已清理并准备重试 removed={len(removed)}",
                        )
                        continue
                    raise
        finally:
            os.chdir(cwd)
            _emit_callback_log(log_callback, "INFO", f"YOLOE 文本编码结束，工作目录已恢复 cwd={cwd}")


def _is_corrupted_torch_archive_error(exc: BaseException) -> bool:
    """判断异常是否来自 PyTorch 权重压缩包损坏。

    主要逻辑：Ultralytics 下载 MobileCLIP 文本编码权重时，如果下载中断，会留下
    不完整的 `.ts` 文件。PyTorch 读取这类文件时通常报 central directory 或
    zip archive 错误。这里只匹配这类明确的坏缓存错误，避免误删其他模型文件。
    参数：`exc` 为模型加载阶段抛出的异常。
    返回值：命中坏缓存特征时返回 True，否则返回 False。
    异常情况：无。
    """

    message = str(exc).lower()
    return (
        ("pytorchstreamreader" in message and "zip archive" in message)
        or "failed finding central directory" in message
        or "not a zip archive" in message
    )


def _remove_cached_text_model_assets(
    cache_dir: Path,
    *,
    log_callback: Callable[[str, str], Any] | None = None,
) -> list[Path]:
    """删除可能损坏的 YOLOE 文本编码缓存文件。

    主要逻辑：只清理 Python phone 运行产物目录下的 MobileCLIP 文本权重和临时下载
    文件，保留其他视觉运行产物。每个删除动作都会记录 DEBUG 日志，方便确认实际处理
    了哪个文件。
    参数：`cache_dir` 为文本权重缓存目录，`log_callback` 为可选 GUI 日志回调。
    返回值：已经删除的文件路径列表。
    异常情况：删除失败时记录 WARNING，继续处理其他文件。
    """

    removed: list[Path] = []
    seen: set[Path] = set()
    for pattern in _TEXT_MODEL_CACHE_PATTERNS:
        for path in cache_dir.glob(pattern):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            try:
                size = path.stat().st_size
                path.unlink()
                removed.append(path)
                _emit_callback_log(
                    log_callback,
                    "DEBUG",
                    f"已删除 YOLOE 文本编码缓存 path={path} size={size}",
                )
            except OSError as exc:
                _emit_callback_log(
                    log_callback,
                    "WARNING",
                    f"删除 YOLOE 文本编码缓存失败 path={path} error={exc}",
                )
    return removed


def _emit_callback_log(log_callback: Callable[[str, str], Any] | None, level: str, message: str) -> None:
    """向可选回调输出日志。"""

    getattr(logger, str(level or "INFO").lower(), logger.info)(message)
    if log_callback is not None:
        log_callback(level, message)


def _mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0 or len(ys) == 0:
        return (0, 0, 0, 0)
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def _bbox_center(bbox: tuple[float, float, float, float] | list[float], *, width: int, height: int) -> dict[str, float]:
    x1, y1, x2, y2 = [float(v) for v in bbox]
    x = (x1 + x2) / 2.0
    y = (y1 + y2) / 2.0
    return {"x": x, "y": y, "x_ratio": x / max(1, width), "y_ratio": y / max(1, height)}


def _direction_hint(center: dict[str, float]) -> str:
    x_ratio = center.get("x_ratio", 0.5)
    if x_ratio < 0.38:
        return "left"
    if x_ratio > 0.62:
        return "right"
    return "front"


def _not_found(object_name: str, reason: str) -> dict[str, Any]:
    return {
        "type": "find_object",
        "object_name": object_name,
        "found": False,
        "confidence": 0.0,
        "reason": reason,
        "source": "yoloe",
    }


def _annotate_find_object(frame_bgr: np.ndarray, detection: dict[str, Any]) -> np.ndarray:
    annotated = frame_bgr.copy()
    if detection.get("found") and detection.get("bbox"):
        x1, y1, x2, y2 = [int(v) for v in detection["bbox"]]
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 255), 2)
        cv2.putText(annotated, str(detection.get("object_name") or "object"), (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    return annotated
