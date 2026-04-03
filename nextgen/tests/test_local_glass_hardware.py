"""本机伪眼镜硬件适配测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from nextgen.apps.glass.execution.device_control import GlassDeviceControl
from nextgen.apps.glass.hardware.local_devices import LocalCameraDevice, LocalMicrophoneDevice, LocalSpeakerDevice
from nextgen.apps.glass.runtime.app import GlassRuntimeApp


class _FakeVideoCapture:
    """用于测试的伪摄像头对象。"""

    def __init__(self, *_args, **_kwargs) -> None:
        self._opened = True
        self._frame = np.zeros((24, 32, 3), dtype=np.uint8)

    def isOpened(self) -> bool:
        """返回摄像头是否打开。"""

        return self._opened

    def set(self, *_args, **_kwargs) -> None:
        """占位设置接口。"""

    def read(self):
        """返回一帧测试画面。"""

        return True, self._frame

    def release(self) -> None:
        """释放资源。"""


def test_local_camera_device_can_capture_frame(monkeypatch) -> None:
    """验证本机摄像头适配器可以抓取并写出一帧画面。"""

    import nextgen.apps.glass.hardware.local_devices as local_devices

    monkeypatch.setattr(local_devices.cv2, "VideoCapture", lambda *_args, **_kwargs: _FakeVideoCapture())
    monkeypatch.setattr(
        local_devices.cv2,
        "imencode",
        lambda *_args, **_kwargs: (True, np.frombuffer(b"fake-jpeg", dtype=np.uint8)),
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = str(Path(tmp_dir) / "frame.jpg")
        result = LocalCameraDevice(camera_index=0).capture_frame(output_path=output_path)
        assert result["width"] == 32
        assert result["height"] == 24
        assert Path(output_path).exists()


def test_local_microphone_device_can_write_wav(monkeypatch) -> None:
    """验证本机麦克风适配器可以写出 WAV 文件。"""

    import nextgen.apps.glass.hardware.local_devices as local_devices

    fake_recording = np.zeros((1600, 1), dtype=np.int16)
    monkeypatch.setattr(local_devices.sd, "rec", lambda *args, **kwargs: fake_recording)
    monkeypatch.setattr(local_devices.sd, "wait", lambda: None)

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = str(Path(tmp_dir) / "mic.wav")
        result = LocalMicrophoneDevice(sample_rate=16000, channels=1).record_audio(0.1, output_path)
        assert result["sample_rate"] == 16000
        assert Path(output_path).exists()


def test_glass_runtime_can_enable_local_hardware_and_speaker(monkeypatch) -> None:
    """验证眼镜运行时可以启用本机硬件并触发喇叭播报。"""

    runtime = GlassRuntimeApp()
    runtime.start()
    runtime.enable_local_camera(camera_index=0)
    runtime.enable_local_microphone()
    runtime.enable_local_speaker()

    called = {}

    def _fake_speak(text: str):
        called["text"] = text
        return {"speaker_backend": "fake", "pid": 123, "text": text}

    runtime.device_control.local_speaker_device.speak_text = _fake_speak
    result = runtime.device_control.execute_speech("测试播报")
    assert result["speaker_backend"] == "fake"
    assert called["text"] == "测试播报"
    assert runtime.sensor_hub.local_camera_device is not None
    assert runtime.sensor_hub.local_microphone_device is not None
