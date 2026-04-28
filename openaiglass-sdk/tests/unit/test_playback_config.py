"""设备级 glass-playback 配置测试。"""

from __future__ import annotations

import json
import sys
import threading
import wave
from pathlib import Path

import pytest

SDK_ROOT = Path(__file__).resolve().parents[2]
GLASS_PLAYBACK_ROOT = SDK_ROOT / "glass-playback"
SERVER_PYTHON_ROOT = SDK_ROOT / "server-python"
for source_root in (GLASS_PLAYBACK_ROOT, SERVER_PYTHON_ROOT):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from protocol.media import MediaFrame

from openaiglass_glass_playback import PlaybackConfig
from openaiglass_glass_playback.glass_device import PlaybackGlassDevice


class _FakeControl:
    """记录 glass-playback 发送的控制消息。"""

    def __init__(self) -> None:
        self.sent_texts: list[str] = []

    def send_text(self, text: str) -> None:
        self.sent_texts.append(text)


class _FakeWsClient:
    """记录 camera stream 发送的二进制帧。"""

    sent_binaries: list[bytes] = []

    def __init__(self, url: str, *, timeout_seconds: float = 30.0) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def send_binary(self, payload: bytes) -> None:
        self.sent_binaries.append(payload)

    def close(self) -> None:
        return


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\0\0" * 160)


def test_playback_config_loads_device_level_config(tmp_path: Path) -> None:
    """配置从设备宿主目录读取，数据资产从 testdata 读取。"""

    app_root = tmp_path / "openaiglass-for-blind"
    config_dir = app_root / "host/glass-playback/config"
    audio_path = app_root / "testdata/audio/trigger.wav"
    _write_wav(audio_path)
    config_path = config_dir / "glass.water_cup.json"
    config_dir.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "device_type": "glass",
                "device_id": "glass-playback-001",
                "pair_token": "pair_playback",
                "control_ws_url": "ws://127.0.0.1:8765/ws/control",
                "sensors": {
                    "trigger_audio": {
                        "path": "testdata/audio/trigger.wav",
                        "format": "wav",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    config = PlaybackConfig.load(config_path, repo_root=tmp_path)

    assert config.device_id == "glass-playback-001"
    assert config.audio_ws_url == "ws://127.0.0.1:8765/ws_audio"
    assert config.trigger_audio.path == audio_path.resolve()
    assert config.outputs is not None
    assert config.outputs.event_log == (app_root / "runs/playback/glass-playback-001/events.jsonl").resolve()


def test_playback_config_requires_trigger_audio(tmp_path: Path) -> None:
    """每个 glass-playback 配置都必须显式提供触发音频。"""

    config_path = tmp_path / "openaiglass-for-blind/host/glass-playback/config/missing.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "device_type": "glass",
                "device_id": "glass-playback-001",
                "pair_token": "pair_playback",
                "control_ws_url": "ws://127.0.0.1:8765/ws/control",
                "sensors": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sensors.trigger_audio"):
        PlaybackConfig.load(config_path, repo_root=tmp_path)


def test_playback_camera_capture_responds_with_configured_image(tmp_path: Path) -> None:
    """抓拍请求从配置图片读取内容，并按真实控制协议回传。"""

    app_root = tmp_path / "openaiglass-for-blind"
    config_dir = app_root / "host/glass-playback/config"
    audio_path = app_root / "testdata/audio/trigger.wav"
    image_path = app_root / "testdata/image/cup.jpg"
    _write_wav(audio_path)
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"fake-jpeg-bytes")
    config_path = config_dir / "glass.water_cup.json"
    config_dir.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "device_type": "glass",
                "device_id": "glass-playback-001",
                "pair_token": "pair_playback",
                "control_ws_url": "ws://127.0.0.1:8765/ws/control",
                "sensors": {
                    "trigger_audio": {
                        "path": "testdata/audio/trigger.wav",
                        "format": "wav",
                    },
                    "camera_capture": {
                        "path": "testdata/image/cup.jpg",
                        "mime_type": "image/jpeg",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config = PlaybackConfig.load(config_path, repo_root=tmp_path)
    device = PlaybackGlassDevice(config)
    control = _FakeControl()

    device._handle_camera_capture(  # noqa: SLF001 - 单元测试直接验证设备协议回包
        control,
        {
            "name": "sensor.camera.capture",
            "session_id": "sess_001",
            "payload": {"request_id": "capture_001"},
        },
    )

    assert len(control.sent_texts) == 1
    message = json.loads(control.sent_texts[0])
    assert message["name"] == "sensor.camera.captured"
    assert message["session_id"] == "sess_001"
    assert message["payload"]["request_id"] == "capture_001"
    assert message["payload"]["ok"] is True
    assert message["payload"]["mime_type"] == "image/jpeg"
    assert message["payload"]["image_base64"] == "ZmFrZS1qcGVnLWJ5dGVz"


def test_playback_realtime_voice_open_saves_session_and_replies_opened(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """测试目标：全双工语音打开请求会保存 session_id，并且命令行只打印收到的消息。

    测试方法：
    1. 构造服务端下发的 `voice.realtime.session.open`。
    2. 执行回放眼镜握手逻辑并读取命令行输出。

    预期结果：
    1. 回放眼镜回复 `voice.realtime.session.opened`。
    2. 命令行只出现收到的 `voice.realtime.session.open`，不打印发送的 opened 消息。
    """

    app_root = tmp_path / "openaiglass-for-blind"
    config_dir = app_root / "host/glass-playback/config"
    audio_path = app_root / "testdata/audio/trigger.wav"
    _write_wav(audio_path)
    config_dir.mkdir(parents=True)
    config_path = config_dir / "glass.realtime.json"
    config_path.write_text(
        json.dumps(
            {
                "device_type": "glass",
                "device_id": "glass-playback-001",
                "pair_token": "pair_playback",
                "control_ws_url": "ws://127.0.0.1:8765/ws/control",
                "sensors": {
                    "trigger_audio": {
                        "path": "testdata/audio/trigger.wav",
                        "format": "wav",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config = PlaybackConfig.load(config_path, repo_root=tmp_path)
    device = PlaybackGlassDevice(config)
    control = _FakeControl()
    messages = iter(
        [
            json.dumps(
                {
                    "name": "voice.realtime.session.open",
                    "session_id": "sess_rt_001",
                    "payload": {"mode": "full_duplex_realtime"},
                }
            )
        ]
    )
    control.recv_text = lambda: next(messages)  # type: ignore[attr-defined]

    device._open_voice_session(control)  # noqa: SLF001 - 单元测试直接验证协议握手

    assert device._session_id == "sess_rt_001"  # noqa: SLF001
    assert len(control.sent_texts) == 1
    reply = json.loads(control.sent_texts[0])
    assert reply["name"] == "voice.realtime.session.opened"
    assert reply["session_id"] == "sess_rt_001"
    assert reply["payload"]["capabilities"]["output_cancel"] is True
    output = capsys.readouterr().out
    assert "收到控制消息 name=voice.realtime.session.open" in output
    assert "voice.realtime.session.opened" not in output


def test_playback_camera_stream_sends_configured_frames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """视频流启动后从配置帧序列读取图片，并发送真实 MediaFrame。"""

    app_root = tmp_path / "openaiglass-for-blind"
    config_dir = app_root / "host/glass-playback/config"
    audio_path = app_root / "testdata/audio/trigger.wav"
    frame_path = app_root / "testdata/image/cup-001.jpg"
    _write_wav(audio_path)
    frame_path.parent.mkdir(parents=True)
    frame_path.write_bytes(b"frame-001")
    config_path = config_dir / "glass.water_cup.json"
    config_dir.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "device_type": "glass",
                "device_id": "glass-playback-001",
                "pair_token": "pair_playback",
                "control_ws_url": "ws://127.0.0.1:8765/ws/control",
                "sensors": {
                    "trigger_audio": {
                        "path": "testdata/audio/trigger.wav",
                        "format": "wav",
                    },
                    "camera_stream": {
                        "frame_interval_ms": 10,
                        "frames": [
                            {
                                "path": "testdata/image/cup-001.jpg",
                                "codec": "jpeg",
                                "t_ms": 0,
                            }
                        ],
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config = PlaybackConfig.load(config_path, repo_root=tmp_path)
    device = PlaybackGlassDevice(config)
    _FakeWsClient.sent_binaries = []
    monkeypatch.setattr("openaiglass_glass_playback.glass_device.WsClient", _FakeWsClient)

    device._camera_stream_loop(  # noqa: SLF001 - 单元测试直接验证设备级推流协议
        "camera_stream_001",
        "ws://127.0.0.1:9000/ws/camera",
        10,
        threading.Event(),
    )

    assert len(_FakeWsClient.sent_binaries) == 1
    frame = MediaFrame.decode(_FakeWsClient.sent_binaries[0])
    assert frame.header["frame_type"] == "camera_frame"
    assert frame.header["stream_id"] == "camera_stream_001"
    assert frame.header["codec"] == "jpeg"
    assert frame.payload == b"frame-001"


def test_playback_audio_save_does_not_block_camera_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """测试目标：保存播放音频时不阻塞抓拍控制消息。

    测试方法：
    1. 配置 `actuators.audio_play.save_audio_to`，并把 `/stream.wav` 下载替换成可控阻塞响应。
    2. 先处理 `actuator.audio.play`，确认保存线程已经开始但控制处理立即返回。
    3. 在下载仍未完成时处理 `sensor.camera.capture`。

    预期结果：
    1. 抓拍回包 `sensor.camera.captured` 会先于下载结束发出。
    2. 允许下载继续后，音频文件会落盘到配置目录。
    """

    app_root = tmp_path / "openaiglass-for-blind"
    config_dir = app_root / "host/glass-playback/config"
    audio_path = app_root / "testdata/audio/trigger.wav"
    image_path = app_root / "testdata/image/cup.jpg"
    _write_wav(audio_path)
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"fake-jpeg-bytes")
    config_dir.mkdir(parents=True)
    config_path = config_dir / "glass.audio_capture.json"
    config_path.write_text(
        json.dumps(
            {
                "device_type": "glass",
                "device_id": "glass-playback-001",
                "pair_token": "pair_playback",
                "control_ws_url": "ws://127.0.0.1:8765/ws/control",
                "sensors": {
                    "trigger_audio": {
                        "path": "testdata/audio/trigger.wav",
                        "format": "wav",
                    },
                    "camera_capture": {
                        "path": "testdata/image/cup.jpg",
                        "mime_type": "image/jpeg",
                    },
                },
                "actuators": {
                    "audio_play": {
                        "mode": "record_and_auto_finish",
                        "save_audio_to": "runs/playback/glass-playback-001/audio",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    download_started = threading.Event()
    allow_download = threading.Event()

    class _BlockingAudioResponse:
        """模拟会阻塞的 `/stream.wav` 下载响应。"""

        def __init__(self) -> None:
            self._sent = False

        def __enter__(self) -> "_BlockingAudioResponse":
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            download_started.set()
            assert allow_download.wait(timeout=1)
            if self._sent:
                return b""
            self._sent = True
            return b"RIFF-fake-wave"

    def _fake_urlopen(url: str, *, timeout: float) -> _BlockingAudioResponse:
        assert "/stream.wav" in url
        assert timeout > 0
        return _BlockingAudioResponse()

    monkeypatch.setattr("openaiglass_glass_playback.glass_device.urlopen", _fake_urlopen)
    config = PlaybackConfig.load(config_path, repo_root=tmp_path)
    device = PlaybackGlassDevice(config)
    control = _FakeControl()
    device._session_id = "sess_001"  # noqa: SLF001 - 单元测试直接验证控制消息时序

    device._handle_control_message(  # noqa: SLF001 - 单元测试直接验证设备协议回包
        control,
        {
            "name": "actuator.audio.play",
            "session_id": "sess_001",
            "stream_id": "stream_audio_001",
            "payload": {"stream_id": "stream_audio_001"},
        },
    )
    assert download_started.wait(timeout=1)

    device._handle_control_message(  # noqa: SLF001 - 单元测试直接验证设备协议回包
        control,
        {
            "name": "sensor.camera.capture",
            "session_id": "sess_001",
            "payload": {"request_id": "capture_001"},
        },
    )

    names_before_download_finished = [json.loads(text)["name"] for text in control.sent_texts]
    assert "sensor.camera.captured" in names_before_download_finished

    allow_download.set()
    device._join_audio_save_threads(timeout_seconds=1)  # noqa: SLF001
    saved_audio = app_root / "runs/playback/glass-playback-001/audio/stream_audio_001.wav"
    assert saved_audio.read_bytes() == b"RIFF-fake-wave"
    output = capsys.readouterr().out
    assert "收到控制消息 name=actuator.audio.play" in output
    assert "收到第一段下行音频 stream_id=stream_audio_001" in output
    assert "actuator.audio.started" not in output


def test_playback_asserts_server_artifact_generated(tmp_path: Path) -> None:
    """测试目标：设备级回放可以断言服务端业务产物已生成。

    测试方法：
    1. 在配置中声明 `assertions.server_artifacts`。
    2. 先创建满足大小要求的产物，再执行断言。
    3. 删除产物后再次执行断言。

    预期结果：
    1. 产物存在时没有断言失败。
    2. 产物不存在时返回包含产物标签的失败信息。
    """

    app_root = tmp_path / "openaiglass-for-blind"
    config_dir = app_root / "host/glass-playback/config"
    audio_path = app_root / "testdata/audio/trigger.wav"
    artifact_path = app_root / "runs/server/sess_001/result.json"
    _write_wav(audio_path)
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text('{"ok": true}', encoding="utf-8")
    config_dir.mkdir(parents=True)
    config_path = config_dir / "glass.assertion.json"
    config_path.write_text(
        json.dumps(
            {
                "device_type": "glass",
                "device_id": "glass-playback-001",
                "pair_token": "pair_playback",
                "control_ws_url": "ws://127.0.0.1:8765/ws/control",
                "sensors": {
                    "trigger_audio": {
                        "path": "testdata/audio/trigger.wav",
                        "format": "wav",
                    }
                },
                "assertions": {
                    "server_artifacts": [
                        {
                            "label": "业务结果",
                            "path": "runs/server/{session_id}/result.json",
                            "min_size_bytes": 2,
                        }
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config = PlaybackConfig.load(config_path, repo_root=tmp_path)
    device = PlaybackGlassDevice(config)
    device._session_id = "sess_001"  # noqa: SLF001

    assert device._evaluate_assertions() == []  # noqa: SLF001

    artifact_path.unlink()
    failures = device._evaluate_assertions()  # noqa: SLF001
    assert len(failures) == 1
    assert "业务结果" in failures[0]
