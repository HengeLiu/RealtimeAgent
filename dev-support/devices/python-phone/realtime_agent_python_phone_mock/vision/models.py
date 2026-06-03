from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MODEL_CACHE: dict[tuple[str, str, str], Any] = {}
_MODEL_LOCK = threading.Lock()


class VisionDependencyError(RuntimeError):
    """视觉依赖不可用错误。"""


class VisionModelError(RuntimeError):
    """视觉模型加载或配置错误。"""


def load_ultralytics_model(*, kind: str, model_path: str, device: str = "auto") -> Any:
    """加载并缓存 ultralytics 模型。

    主要逻辑：按 kind/model_path/device 建立进程内缓存；首次加载时检查依赖和模型文件，
    并尽量把模型移动到指定设备。
    参数：`kind` 为 yolo/yoloe，`model_path` 为模型文件路径，`device` 为 auto/cpu/mps/cuda。
    返回值：ultralytics 模型实例。
    异常情况：依赖缺失、模型文件缺失或加载失败时抛出明确异常。
    """

    resolved = str(Path(model_path).expanduser())
    path = Path(resolved)
    if not path.exists():
        raise VisionModelError(f"视觉模型文件不存在: {resolved}")
    cache_key = (kind.lower(), resolved, device.lower())
    with _MODEL_LOCK:
        cached = _MODEL_CACHE.get(cache_key)
        if cached is not None:
            return cached
        model_cls = _resolve_model_class(kind)
        logger.info("vision.model.loading kind=%s path=%s device=%s", kind, resolved, device)
        model = model_cls(resolved)
        selected_device = _select_device(device)
        if selected_device:
            try:
                model.to(selected_device)
            except Exception as exc:  # noqa: BLE001 - 设备不可用时给出清晰错误
                raise VisionModelError(f"视觉模型切换设备失败 device={selected_device}: {exc}") from exc
        _MODEL_CACHE[cache_key] = model
        logger.info("vision.model.loaded kind=%s path=%s device=%s", kind, resolved, selected_device or "default")
        return model


def _resolve_model_class(kind: str) -> Any:
    """解析 ultralytics 模型类。

    参数：`kind` 为 yolo 或 yoloe。
    返回值：模型类对象。
    异常情况：未安装 ultralytics 时抛出 VisionDependencyError。
    """

    try:
        if kind.lower() == "yoloe":
            try:
                from ultralytics import YOLOE  # type: ignore

                return YOLOE
            except Exception:
                from ultralytics import YOLO  # type: ignore

                return YOLO
        from ultralytics import YOLO  # type: ignore

        return YOLO
    except ModuleNotFoundError as exc:
        raise VisionDependencyError(
            "缺少 Python phone 视觉依赖，请在运行 phone 端的 Python 环境中执行: "
            "uv pip install -r dev-support/devices/python-phone/requirements.vision.txt"
        ) from exc


def _select_device(device: str) -> str:
    """选择模型运行设备。

    参数：`device` 为 auto/cpu/mps/cuda/cuda:0。
    返回值：ultralytics 可接受的设备字符串；auto 时优先 CUDA，否则使用 CPU。
    异常情况：无。
    """

    normalized = str(device or "auto").strip().lower()
    if normalized == "auto":
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            return "cpu"
        return "cpu"
    if normalized in {"default", "none"}:
        return ""
    return normalized
