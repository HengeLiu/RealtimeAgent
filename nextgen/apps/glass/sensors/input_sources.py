"""眼镜感知总线的具体输入源实现。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

from nextgen.apps.glass.hardware.local_devices import LocalCameraDevice, LocalMicrophoneDevice


class GlassSensorInputSource(ABC):
    """感知输入源抽象接口。

    主要功能：
    - 为眼镜端感知总线提供不同来源的输入实现
    - 隔离 UI 注入与真实硬件两类输入方式

    主要方法：
    - `snapshot`：返回当前输入源状态快照
    - 各类 `capture` / `record` / `inject` 方法：由子类按需实现

    主要属性：
    - `source_name`：输入源名称
    """

    source_name: str = "unknown"

    def snapshot(self) -> Dict[str, Any]:
        """返回输入源状态快照。"""

        return {"source_name": self.source_name}

    def inject_text(self, text: str) -> Dict[str, Any]:
        """注入一段文本。

        异常情况：
        - 默认实现不支持时抛出 `NotImplementedError`
        """

        raise NotImplementedError(f"{self.source_name} 不支持文本注入。")

    def inject_image_path(self, image_path: str) -> Dict[str, Any]:
        """注入一张图片路径。"""

        raise NotImplementedError(f"{self.source_name} 不支持图片注入。")

    def inject_video_path(self, video_path: str) -> Dict[str, Any]:
        """注入一段视频路径。"""

        raise NotImplementedError(f"{self.source_name} 不支持视频注入。")

    def configure_camera(self, camera_index: int = 0, preferred_width: Optional[int] = None, preferred_height: Optional[int] = None) -> None:
        """配置摄像头。"""

        raise NotImplementedError(f"{self.source_name} 不支持摄像头配置。")

    def capture_camera_frame(self, output_path: Optional[str] = None) -> Dict[str, Any]:
        """采集摄像头画面。"""

        raise NotImplementedError(f"{self.source_name} 不支持摄像头采集。")

    def configure_microphone(self, sample_rate: int = 16000, channels: int = 1, dtype: str = "int16") -> None:
        """配置麦克风。"""

        raise NotImplementedError(f"{self.source_name} 不支持麦克风配置。")

    def record_microphone_audio(self, duration_sec: float, output_path: str) -> Dict[str, Any]:
        """录制麦克风音频。"""

        raise NotImplementedError(f"{self.source_name} 不支持录音。")

    def start_microphone_recording(self, output_path: str) -> Dict[str, Any]:
        """开始录音。"""

        raise NotImplementedError(f"{self.source_name} 不支持开始录音。")

    def stop_microphone_recording(self) -> Dict[str, Any]:
        """停止录音。"""

        raise NotImplementedError(f"{self.source_name} 不支持停止录音。")

    def start_microphone_stream(self, on_chunk, blocksize: int = 1600):
        """开始麦克风流。"""

        raise NotImplementedError(f"{self.source_name} 不支持实时音频流。")


class UiSimulationSensorSource(GlassSensorInputSource):
    """通过 UI 上传样例数据的感知输入源。

    主要功能：
    - 接收浏览器页面上传的文本、图片、视频作为样例输入
    - 保存最近一次注入的数据，供 UI 查看和后续模拟流程复用

    主要属性：
    - `records`：保存已注入样例数据的列表
    """

    source_name = "ui_simulation"

    def __init__(self) -> None:
        """初始化 UI 模拟输入源。"""

        self.records: Dict[str, List[Dict[str, Any]]] = {"texts": [], "images": [], "videos": []}

    def inject_text(self, text: str) -> Dict[str, Any]:
        """保存一段由 UI 注入的文本。"""

        record = {
            "text": text,
            "source": self.source_name,
            "created_at": datetime.now().astimezone().isoformat(),
        }
        self.records["texts"].append(record)
        return record

    def inject_image_path(self, image_path: str) -> Dict[str, Any]:
        """保存一张由 UI 注入的图片路径。"""

        record = {
            "image_path": image_path,
            "source": self.source_name,
            "created_at": datetime.now().astimezone().isoformat(),
        }
        self.records["images"].append(record)
        return record

    def inject_video_path(self, video_path: str) -> Dict[str, Any]:
        """保存一段由 UI 注入的视频路径。"""

        record = {
            "video_path": video_path,
            "source": self.source_name,
            "created_at": datetime.now().astimezone().isoformat(),
        }
        self.records["videos"].append(record)
        return record

    def snapshot(self) -> Dict[str, Any]:
        """返回 UI 注入输入源快照。"""

        return {
            "source_name": self.source_name,
            "texts": list(self.records["texts"]),
            "images": list(self.records["images"]),
            "videos": list(self.records["videos"]),
        }


class HardwareSensorSource(GlassSensorInputSource):
    """真实硬件输入源。

    主要功能：
    - 统一封装本机摄像头、麦克风等真实硬件输入能力
    - 为后续替换成真眼镜硬件驱动提供稳定接口

    主要属性：
    - `local_camera_device`：本机摄像头适配器
    - `local_microphone_device`：本机麦克风适配器
    """

    source_name = "hardware"

    def __init__(self) -> None:
        """初始化真实硬件输入源。"""

        self.local_camera_device: Optional[LocalCameraDevice] = None
        self.local_microphone_device: Optional[LocalMicrophoneDevice] = None

    def configure_camera(self, camera_index: int = 0, preferred_width: Optional[int] = None, preferred_height: Optional[int] = None) -> None:
        """配置本机摄像头适配器。"""

        self.local_camera_device = LocalCameraDevice(
            camera_index=camera_index,
            preferred_width=preferred_width,
            preferred_height=preferred_height,
        )

    def capture_camera_frame(self, output_path: Optional[str] = None) -> Dict[str, Any]:
        """采集一帧本机摄像头画面。"""

        if self.local_camera_device is None:
            raise RuntimeError("本机摄像头尚未配置。")
        return self.local_camera_device.capture_frame(output_path=output_path)

    def configure_microphone(self, sample_rate: int = 16000, channels: int = 1, dtype: str = "int16") -> None:
        """配置本机麦克风适配器。"""

        self.local_microphone_device = LocalMicrophoneDevice(
            sample_rate=sample_rate,
            channels=channels,
            dtype=dtype,
        )

    def record_microphone_audio(self, duration_sec: float, output_path: str) -> Dict[str, Any]:
        """录制一段本机麦克风音频。"""

        if self.local_microphone_device is None:
            raise RuntimeError("本机麦克风尚未配置。")
        return self.local_microphone_device.record_audio(duration_sec=duration_sec, output_path=output_path)

    def start_microphone_recording(self, output_path: str) -> Dict[str, Any]:
        """启动一段可停止的本机录音。"""

        if self.local_microphone_device is None:
            raise RuntimeError("本机麦克风尚未配置。")
        return self.local_microphone_device.start_recording(output_path=output_path)

    def stop_microphone_recording(self) -> Dict[str, Any]:
        """停止当前本机录音。"""

        if self.local_microphone_device is None:
            raise RuntimeError("本机麦克风尚未配置。")
        return self.local_microphone_device.stop_recording()

    def start_microphone_stream(self, on_chunk, blocksize: int = 1600):
        """启动实时本机麦克风流。"""

        if self.local_microphone_device is None:
            raise RuntimeError("本机麦克风尚未配置。")
        return self.local_microphone_device.start_streaming(on_chunk=on_chunk, blocksize=blocksize)

    def snapshot(self) -> Dict[str, Any]:
        """返回真实硬件输入源快照。"""

        return {
            "source_name": self.source_name,
            "camera_configured": self.local_camera_device is not None,
            "microphone_configured": self.local_microphone_device is not None,
        }
