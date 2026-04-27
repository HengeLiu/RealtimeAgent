"""设备级回放传感器资产读取。"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(slots=True)
class CameraFrameAsset:
    """一次摄像头帧输入。

    主要属性：
    1. `payload`：帧图片字节。
    2. `codec`：帧编码，当前常用 `jpeg`、`png`、`webp`。
    3. `t_ms`：相对流开始时间，未配置时由发送端按固定间隔计算。
    """

    payload: bytes
    codec: str
    t_ms: int | None = None


ResolvePath = Callable[[object], Path]


def load_camera_frames(
    raw: object,
    *,
    resolve_path: ResolvePath,
    frame_interval_ms: int,
) -> list[CameraFrameAsset]:
    """从 `camera_stream` 配置读取摄像头帧。

    主要逻辑：
    1. 支持 `frames` 图片帧序列。
    2. 支持 `path` 指向单张图片。
    3. 支持 `path` 指向 MP4，并通过本机 `ffmpeg` 解出 JPEG 帧。

    参数：
    1. `raw`：`sensors.camera_stream` 原始配置。
    2. `resolve_path`：调用方提供的资产路径解析函数。
    3. `frame_interval_ms`：未配置 `t_ms` 时使用的帧间隔。

    返回值：
    1. 摄像头帧列表。

    异常情况：
    1. 配置非法、资产不存在或 MP4 解码工具缺失时抛出异常。
    """

    if not isinstance(raw, dict):
        raise ValueError("glass-playback 配置缺少 sensors.camera_stream")

    frames = raw.get("frames")
    if isinstance(frames, list) and frames:
        return _load_frame_sequence(frames, resolve_path=resolve_path, frame_interval_ms=frame_interval_ms)

    path = resolve_path(raw.get("path"))
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return [
            CameraFrameAsset(
                payload=path.read_bytes(),
                codec=str(raw.get("codec") or _codec_from_suffix(path)).strip().lower(),
                t_ms=0,
            )
        ]
    if suffix == ".mp4":
        return _load_mp4_frames(path, frame_interval_ms=frame_interval_ms)
    raise ValueError(f"camera_stream 不支持的资产格式: {path}")


def _load_frame_sequence(
    frames: list[object],
    *,
    resolve_path: ResolvePath,
    frame_interval_ms: int,
) -> list[CameraFrameAsset]:
    result: list[CameraFrameAsset] = []
    for index, item in enumerate(frames):
        if not isinstance(item, dict):
            raise ValueError(f"camera_stream.frames[{index}] 必须是 JSON object")
        path = resolve_path(item.get("path"))
        result.append(
            CameraFrameAsset(
                payload=path.read_bytes(),
                codec=str(item.get("codec") or _codec_from_suffix(path)).strip().lower(),
                t_ms=int(item["t_ms"]) if isinstance(item.get("t_ms"), (int, float)) else index * frame_interval_ms,
            )
        )
    return result


def _load_mp4_frames(path: Path, *, frame_interval_ms: int) -> list[CameraFrameAsset]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("camera_stream 使用 MP4 时需要本机安装 ffmpeg")
    fps = max(1000 / max(frame_interval_ms, 1), 0.1)
    with tempfile.TemporaryDirectory(prefix="glass-playback-frames-") as tmp:
        output_pattern = Path(tmp) / "frame_%06d.jpg"
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-vf",
                f"fps={fps:.3f}",
                str(output_pattern),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"ffmpeg 解码 camera_stream 失败: {completed.stderr.strip()}")
        frame_paths = sorted(Path(tmp).glob("frame_*.jpg"))
        if not frame_paths:
            raise RuntimeError(f"MP4 未解出任何摄像头帧: {path}")
        return [
            CameraFrameAsset(
                payload=frame_path.read_bytes(),
                codec="jpeg",
                t_ms=index * frame_interval_ms,
            )
            for index, frame_path in enumerate(frame_paths)
        ]


def _codec_from_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "jpeg"
    if suffix == ".png":
        return "png"
    if suffix == ".webp":
        return "webp"
    return suffix.lstrip(".") or "binary"
