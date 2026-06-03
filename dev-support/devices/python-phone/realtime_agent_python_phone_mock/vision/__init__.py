"""Python phone 本地视觉识别模块。

主要功能：为 peer video 任务提供找物和红绿灯识别的统一入口。模块内部按需加载
YOLO/YOLOE 依赖，避免没有安装视觉依赖时影响普通 phone mock 能力。
"""

from .config import VisionConfig
from .processor import VisionProcessor, build_vision_processor
from .result import VisionFrameResult

__all__ = ["VisionConfig", "VisionFrameResult", "VisionProcessor", "build_vision_processor"]
