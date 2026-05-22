from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MultimodalMessagePolicy:
    """Vision 多模态消息策略。

    主要功能：集中描述 Vision 链路是否允许把显式视觉资产拼入
    provider message，以及单轮图片数量、大小和抓拍次数限制。
    主要属性：`enabled` 控制总开关，`attach_visual_assets` 控制显式视觉资产
    是否进入 follow-up message。
    """

    enabled: bool = False
    attach_visual_assets: bool = False
    max_images_per_turn: int = 4
    image_freshness_seconds: float = 2.0
    max_image_base64_bytes: int = 7_500_000
    max_capture_photo_calls_per_turn: int = 1
    video_enabled: bool = False
    video_prefer_native_video: bool = True
    video_max_inline_bytes: int = 50_000_000
    video_max_duration_seconds: float = 120.0
    video_sample_fps: float = 1.0
    video_max_frames: int = 16
    video_frame_jpeg_quality: int = 85
