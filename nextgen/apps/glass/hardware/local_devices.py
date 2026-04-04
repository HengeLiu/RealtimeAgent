"""眼镜端本机硬件适配实现。"""

from __future__ import annotations

import io
import queue
import subprocess
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import cv2
import numpy as np
import sounddevice as sd


@dataclass
class LocalCameraDevice:
    """本机摄像头适配器。

    主要功能：
    - 打开本机摄像头并抓取一帧画面
    - 按需写出 JPEG 文件，供伪眼镜联调使用

    主要属性：
    - camera_index：摄像头编号
    - preferred_width：期望宽度
    - preferred_height：期望高度
    """

    camera_index: int = 0
    preferred_width: Optional[int] = None
    preferred_height: Optional[int] = None

    def capture_frame(self, output_path: Optional[str] = None) -> Dict[str, Any]:
        """采集一帧本机摄像头画面。

        主要逻辑：
        - 按当前配置打开摄像头
        - 读取一帧 BGR 图像
        - 若提供输出路径，则写出 JPEG 文件

        参数：
        - output_path：可选的 JPEG 输出路径

        返回值：
        - 包含分辨率、文件路径和 JPEG 字节大小的结果字典

        异常情况：
        - 无法打开摄像头时抛出 `RuntimeError`
        - 无法读取帧时抛出 `RuntimeError`
        """

        capture = cv2.VideoCapture(self.camera_index)
        if not capture.isOpened():
            raise RuntimeError(f"无法打开本机摄像头: index={self.camera_index}")

        try:
            if self.preferred_width:
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.preferred_width)
            if self.preferred_height:
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.preferred_height)
            ok, frame = capture.read()
            if not ok or frame is None:
                raise RuntimeError("本机摄像头未能读取到有效帧。")

            encode_ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            if not encode_ok:
                raise RuntimeError("摄像头帧 JPEG 编码失败。")

            saved_path = None
            if output_path:
                path = Path(output_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(encoded.tobytes())
                saved_path = str(path)

            return {
                "camera_index": self.camera_index,
                "width": int(frame.shape[1]),
                "height": int(frame.shape[0]),
                "channels": int(frame.shape[2]) if len(frame.shape) == 3 else 1,
                "jpeg_size": int(encoded.size),
                "output_path": saved_path,
            }
        finally:
            capture.release()


@dataclass
class LocalMicrophoneDevice:
    """本机麦克风适配器。

    主要功能：
    - 使用本机默认麦克风录制短音频
    - 写出 WAV 文件，供后续 VAD / ASR 联调使用

    主要属性：
    - sample_rate：采样率
    - channels：声道数
    - dtype：采样数据类型
    """

    sample_rate: int = 16000
    channels: int = 1
    dtype: str = "int16"
    _recording_stream: Any = None
    _recording_frames: list[bytes] | None = None
    _recording_output_path: str | None = None

    def record_audio(self, duration_sec: float, output_path: str) -> Dict[str, Any]:
        """录制一段本机麦克风音频。

        主要逻辑：
        - 使用默认麦克风录制指定时长
        - 将录音结果写出为 WAV 文件

        参数：
        - duration_sec：录音时长，单位秒
        - output_path：WAV 输出路径

        返回值：
        - 包含采样率、时长和输出路径的结果字典

        异常情况：
        - 若录音失败，底层异常会直接向上抛出
        """

        frame_count = max(1, int(duration_sec * self.sample_rate))
        recording = sd.rec(frame_count, samplerate=self.sample_rate, channels=self.channels, dtype=self.dtype)
        sd.wait()

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(np.dtype(self.dtype).itemsize)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(np.asarray(recording).tobytes())

        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "duration_sec": duration_sec,
            "frame_count": frame_count,
            "output_path": str(path),
        }

    def start_recording(self, output_path: str) -> Dict[str, Any]:
        """启动一段可停止的录音。"""

        if self._recording_stream is not None:
            raise RuntimeError("本机麦克风已处于录音中。")

        self._recording_frames = []
        self._recording_output_path = output_path

        def _callback(indata, _frames, _time, _status):
            if self._recording_frames is not None:
                self._recording_frames.append(bytes(indata))

        self._recording_stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=self.dtype,
            callback=_callback,
        )
        self._recording_stream.start()
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "output_path": output_path,
            "status": "recording",
        }

    def stop_recording(self) -> Dict[str, Any]:
        """停止当前录音并写出 WAV 文件。"""

        if self._recording_stream is None or self._recording_frames is None or self._recording_output_path is None:
            raise RuntimeError("当前没有正在进行的录音。")

        stream = self._recording_stream
        output_path = self._recording_output_path
        frames = self._recording_frames

        self._recording_stream = None
        self._recording_frames = None
        self._recording_output_path = None

        stream.stop()
        stream.close()

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(np.dtype(self.dtype).itemsize)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(b"".join(frames))

        frame_count = len(b"".join(frames)) // max(1, self.channels * np.dtype(self.dtype).itemsize)
        duration_sec = round(frame_count / max(1, self.sample_rate), 3)
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frame_count": frame_count,
            "duration_sec": duration_sec,
            "output_path": str(path),
            "status": "stopped",
        }

    def start_streaming(self, on_chunk: Any, blocksize: int = 1600) -> Any:
        """启动实时音频流采集。"""

        def _callback(indata, _frames, _time, _status):
            on_chunk(bytes(indata))

        stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=self.dtype,
            blocksize=blocksize,
            callback=_callback,
        )
        stream.start()
        return stream


@dataclass
class LocalSpeakerDevice:
    """本机喇叭适配器。

    主要功能：
    - 优先使用 macOS `say` 命令播报文本
    - 若系统不存在 `say`，则退化为仅返回播报元信息

    主要属性：
    - volume：期望音量，仅作为状态记录
    """

    volume: int = 50
    _stream_queue: "queue.Queue[tuple[bytes, int]]" | None = None
    _stream_thread: threading.Thread | None = None
    _stream_stop: threading.Event | None = None

    def speak_text(self, text: str) -> Dict[str, Any]:
        """通过本机喇叭播报文本。

        主要逻辑：
        - 在 macOS 上调用 `say`
        - 使用非阻塞方式启动播报进程

        参数：
        - text：待播报文本

        返回值：
        - 包含播报方式、进程号和文本的结果字典
        """

        try:
            process = subprocess.Popen(["say", text])
            return {
                "speaker_backend": "macos_say",
                "pid": process.pid,
                "text": text,
                "volume": self.volume,
            }
        except FileNotFoundError:
            return {
                "speaker_backend": "noop",
                "pid": None,
                "text": text,
                "volume": self.volume,
            }

    def start_pcm_stream(self) -> None:
        """启动 PCM 流式播放线程。"""

        if self._stream_thread is not None and self._stream_thread.is_alive():
            return
        self._stream_queue = queue.Queue()
        self._stream_stop = threading.Event()

        def _worker():
            while self._stream_stop is not None and not self._stream_stop.is_set():
                try:
                    pcm_bytes, sample_rate = self._stream_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                audio = np.frombuffer(pcm_bytes, dtype=np.int16)
                if audio.size == 0:
                    continue
                sd.play(audio, samplerate=sample_rate, blocking=True)

        self._stream_thread = threading.Thread(target=_worker, daemon=True)
        self._stream_thread.start()

    def push_pcm_chunk(self, pcm_bytes: bytes, sample_rate: int) -> Dict[str, Any]:
        """推送一段 PCM 音频块到播放队列。"""

        self.start_pcm_stream()
        assert self._stream_queue is not None
        self._stream_queue.put((pcm_bytes, sample_rate))
        return {
            "speaker_backend": "sounddevice_pcm_stream",
            "sample_rate": sample_rate,
            "chunk_size": len(pcm_bytes),
        }

    def stop_pcm_stream(self) -> Dict[str, Any]:
        """停止当前 PCM 播放。"""

        if self._stream_stop is not None:
            self._stream_stop.set()
        sd.stop()
        self._stream_queue = None
        self._stream_thread = None
        self._stream_stop = None
        return {"speaker_backend": "sounddevice_pcm_stream", "status": "stopped"}
