from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from ..vision_mock import build_mock_result, fork_yolo_mock
from .config import VisionConfig
from .find_object import FindObjectDetector
from .result import VisionFrameResult
from .traffic_light import TrafficLightDetector

logger = logging.getLogger(__name__)


class VisionProcessor:
    """peer video 视觉处理器。

    主要功能：根据 purpose 调度 mock、找物 YOLOE 或红绿灯 YOLO，并把每帧结果转换
    成 `PeerVideoReceiver` 可消费的统一结构。
    """

    def __init__(self, config: VisionConfig) -> None:
        self.config = config
        self.provider = config.provider
        self.log_callback: Callable[[str, str], Any] | None = None
        self.purpose = "find_object"
        self.object_name = "目标物"
        self.frame_index = 0
        self.last_result: dict[str, Any] | None = None
        self.find_object_detector: FindObjectDetector | None = None
        self.traffic_light_detector: TrafficLightDetector | None = None

    async def prepare_session(self, *, purpose: str, object_name: str) -> None:
        """准备一次 peer video 视觉任务。

        参数：`purpose` 为 find_object 或 traffic_light，`object_name` 为找物目标。
        返回值：无。
        异常情况：真实模型配置错误或依赖缺失时向上抛出。
        """

        self.purpose = purpose or "find_object"
        self.object_name = object_name or "目标物"
        self.frame_index = 0
        self.last_result = None
        self._emit_log("INFO", f"vision session 准备开始 provider={self.provider} purpose={self.purpose} object_name={self.object_name}")
        if self.provider == "mock":
            self._emit_log("INFO", f"vision session 使用 mock provider purpose={self.purpose}")
            return
        if self.purpose == "traffic_light":
            self.traffic_light_detector = TrafficLightDetector(self.config.traffic_light, device=self.config.device)
            self._emit_log("INFO", f"红绿灯模型准备开始 device={self.config.device} model={self.config.traffic_light.model_path}")
            await asyncio.to_thread(self.traffic_light_detector.prepare)
            self._emit_log("INFO", "红绿灯模型准备完成")
        else:
            self.find_object_detector = FindObjectDetector(
                self.config.find_object,
                device=self.config.device,
                object_name=self.object_name,
                log_callback=self.log_callback,
            )
            self._emit_log("INFO", f"找物模型准备开始 device={self.config.device} model={self.config.find_object.model_path}")
            await asyncio.to_thread(self.find_object_detector.prepare, self.object_name)
            self._emit_log("INFO", "找物模型准备完成")

    async def process_frame(self, frame: bytes, *, frame_count: int) -> VisionFrameResult:
        """处理一帧 peer video JPEG。

        参数：`frame` 为 JPEG/PNG 字节，`frame_count` 为当前帧序号。
        返回值：单帧识别结果。
        异常情况：解码或模型推理异常时向上抛出。
        """

        self.frame_index += 1
        if self.provider == "mock":
            self._emit_log("DEBUG", f"mock vision 开始处理帧 frame_count={frame_count} bytes={len(frame)}")
            detection = await fork_yolo_mock(frame, purpose=self.purpose, object_name=self.object_name)
            self.last_result = detection
            return VisionFrameResult(detection=detection, should_complete=False, metrics={"provider": "mock"})
        if self.frame_index % max(1, self.config.frame_stride) != 0 and self.last_result is not None:
            self._emit_log("DEBUG", f"vision 跳过帧 frame_count={frame_count} frame_stride={self.config.frame_stride}")
            return VisionFrameResult(detection=dict(self.last_result), should_complete=False, metrics={"skipped": True})
        start = time.perf_counter()
        self._emit_log("DEBUG", f"vision 解码帧开始 frame_count={frame_count} bytes={len(frame)}")
        bgr = await asyncio.to_thread(_decode_frame, frame)
        decode_ms = int((time.perf_counter() - start) * 1000)
        self._emit_log("DEBUG", f"vision 解码帧完成 frame_count={frame_count} decode_ms={decode_ms}")
        if self.purpose == "traffic_light":
            detector = self.traffic_light_detector or TrafficLightDetector(self.config.traffic_light, device=self.config.device)
            self.traffic_light_detector = detector
            self._emit_log("DEBUG", f"红绿灯推理开始 frame_count={frame_count}")
            result = await asyncio.to_thread(detector.process, bgr, frame_count=frame_count)
        else:
            detector = self.find_object_detector or FindObjectDetector(
                self.config.find_object,
                device=self.config.device,
                object_name=self.object_name,
                log_callback=self.log_callback,
            )
            self.find_object_detector = detector
            self._emit_log("DEBUG", f"找物推理开始 frame_count={frame_count} object_name={self.object_name}")
            result = await asyncio.to_thread(detector.process, bgr, frame_count=frame_count)
        result.metrics = {"decode_ms": decode_ms, **dict(result.metrics)}
        self.last_result = dict(result.detection)
        await asyncio.to_thread(self._save_annotated_frame, result.annotated_image)
        logger.info(
            "vision.frame.processed purpose=%s provider=%s frame_count=%s elapsed_ms=%s",
            self.purpose,
            result.detection.get("source") or self.provider,
            frame_count,
            int((time.perf_counter() - start) * 1000),
        )
        self._emit_log(
            "INFO",
            (
                f"vision 帧处理完成 frame_count={frame_count} provider={result.detection.get('source') or self.provider} "
                f"elapsed_ms={int((time.perf_counter() - start) * 1000)}"
            ),
        )
        return result

    async def build_final_result(self, *, frame_count: int, last_detection: dict[str, Any] | None) -> dict[str, Any]:
        """生成任务最终结果。"""

        if self.provider == "mock":
            return build_mock_result(purpose=self.purpose, object_name=self.object_name, frame_count=frame_count, last_detection=last_detection)
        if self.purpose == "traffic_light" and self.traffic_light_detector is not None:
            return self.traffic_light_detector.build_final_result(frame_count=frame_count, last_detection=last_detection)
        if self.find_object_detector is not None:
            return self.find_object_detector.build_final_result(frame_count=frame_count, last_detection=last_detection)
        return build_mock_result(purpose=self.purpose, object_name=self.object_name, frame_count=frame_count, last_detection=last_detection)

    def _save_annotated_frame(self, image: Any | None) -> None:
        if image is None or not self.config.save_annotated_frame:
            return
        path = Path(self.config.save_annotated_frame).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), image)
        self._emit_log("DEBUG", f"vision 标注帧已保存 path={path}")

    def _emit_log(self, level: str, message: str) -> None:
        """输出视觉处理日志，并同步到可选 GUI 日志面板。"""

        normalized = str(level or "INFO").upper()
        getattr(logger, normalized.lower(), logger.info)(message)
        if self.log_callback is not None:
            self.log_callback(normalized, message)


def build_vision_processor(config: VisionConfig | dict[str, Any] | None) -> VisionProcessor:
    """创建视觉处理器。"""

    if isinstance(config, VisionConfig):
        return VisionProcessor(config)
    return VisionProcessor(VisionConfig.from_mapping(config if isinstance(config, dict) else None))


def _decode_frame(frame: bytes) -> np.ndarray:
    """解码 JPEG/PNG 帧为 BGR 图像。"""

    arr = np.frombuffer(frame, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("peer video frame decode failed")
    return image
