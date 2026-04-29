"""设备级 glass-playback 配置测试。"""

from __future__ import annotations

import json
import sys
import threading
import tomllib
import wave
from argparse import Namespace
from pathlib import Path

import pytest

SDK_ROOT = Path(__file__).resolve().parents[2]
GLASS_PLAYBACK_ROOT = SDK_ROOT / "glass-playback"
SERVER_PYTHON_ROOT = SDK_ROOT / "server-python"
for source_root in (GLASS_PLAYBACK_ROOT, SERVER_PYTHON_ROOT):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from protocol.media import MediaFrame

from openaiglasses.cli.glass import load_playback_runner
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


def test_glass_playback_can_load_from_installed_package_without_sdk_root() -> None:
    """测试目标：验证功能开发者不需要指定 `--sdk-root` 也能加载回放运行时。

    测试方法：
    1. 构造一个指向不存在 SDK 根目录的参数对象。
    2. 调用统一 CLI 的 playback 入口加载函数。
    3. 检查返回的运行函数来自已安装或当前可导入的 playback 包。

    预期结果：
    1. 加载过程不访问 `--sdk-root`。
    2. 返回可调用的 `run_playback` 函数。
    """

    args = Namespace(
        repo_root="",
        app_root="",
        sdk_root="/path/that/should/not/be/needed",
        project_dir="",
        idf_root="",
    )

    runner = load_playback_runner(args)

    assert callable(runner)
    assert runner.__module__ == "openaiglass_glass_playback.cli"


def test_sdk_package_includes_glass_playback_runtime() -> None:
    """测试目标：验证 SDK 安装包会包含 glass-playback 运行时。

    测试方法：
    1. 读取 SDK 根目录的 `pyproject.toml`。
    2. 检查 setuptools 包发现路径和 include 列表。

    预期结果：
    1. `glass-playback` 会作为包发现根目录。
    2. `openaiglass_glass_playback*` 会被纳入安装包。
    """

    pyproject = tomllib.loads((SDK_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_find = pyproject["tool"]["setuptools"]["packages"]["find"]

    assert "glass-playback" in package_find["where"]
    assert "openaiglass_glass_playback*" in package_find["include"]


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


def test_playback_audio_can_play_without_persistent_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """测试目标：验证 glass-playback 可直接播放服务端下行音频。

    测试方法：
    1. 配置 `actuators.audio_play.mode=play_and_auto_finish`，不配置 `save_audio_to`。
    2. 将 `/stream.wav` 下载和本机播放器替换成测试替身。
    3. 处理一次 `actuator.audio.play` 控制消息并等待后台线程结束。

    预期结果：
    1. SDK 会调用配置的播放器命令。
    2. 播放过程只使用临时文件，结束后不生成持久化音频文件。
    3. 设备会回报 `actuator.audio.started` 和 `actuator.audio.finished`。
    """

    app_root = tmp_path / "openaiglass-for-blind"
    config_dir = app_root / "host/glass-playback/config"
    audio_path = app_root / "testdata/audio/trigger.wav"
    config_dir.mkdir(parents=True)
    _write_wav(audio_path)
    config_path = config_dir / "glass.audio_play.json"
    config_path.write_text(
        json.dumps(
            {
                "device_id": "glass-playback-001",
                "pair_token": "pair-playback",
                "control_ws_url": "ws://127.0.0.1:8765/ws/control",
                "startup": {"wait_for_binding": False},
                "sensors": {
                    "trigger_audio": {
                        "path": "testdata/audio/trigger.wav",
                        "sample_rate_hz": 16000,
                        "channels": 1,
                        "chunk_ms": 20,
                    }
                },
                "actuators": {
                    "audio_play": {
                        "mode": "play_and_auto_finish",
                        "player_command": "fake-player --quiet",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    class _StaticAudioResponse:
        def __init__(self) -> None:
            self._chunks = [b"RIFF-fake-wave", b""]

        def __enter__(self) -> "_StaticAudioResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self, _size: int) -> bytes:
            return self._chunks.pop(0)

    played_commands: list[list[str]] = []
    temp_paths_seen: list[Path] = []

    def _fake_urlopen(url: str, *, timeout: float) -> _StaticAudioResponse:
        assert "/stream.wav" in url
        assert timeout > 0
        return _StaticAudioResponse()

    def _fake_run(command: list[str], *, check: bool) -> None:
        assert check is True
        played_commands.append(command)
        temp_path = Path(command[-1])
        temp_paths_seen.append(temp_path)
        assert temp_path.read_bytes() == b"RIFF-fake-wave"

    monkeypatch.setattr("openaiglass_glass_playback.glass_device.urlopen", _fake_urlopen)
    monkeypatch.setattr("openaiglass_glass_playback.glass_device.subprocess.run", _fake_run)

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
    device._join_audio_save_threads(timeout_seconds=1)  # noqa: SLF001

    assert played_commands
    assert played_commands[0][:2] == ["fake-player", "--quiet"]
    assert temp_paths_seen and not temp_paths_seen[0].exists()
    sent_names = [json.loads(text)["name"] for text in control.sent_texts]
    assert sent_names == ["actuator.audio.started", "actuator.audio.finished"]
    assert not (app_root / "runs/playback/glass-playback-001/audio/stream_audio_001.wav").exists()


def test_playback_audio_uses_streaming_player_when_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """测试目标：验证 glass-playback 的直接播放模式可以边下载边写入播放器。

    测试方法：
    1. 配置 `play_and_auto_finish` 和支持 stdin 的 `ffplay` 命令。
    2. 将 `/stream.wav` 响应拆成 WAV 头和音频数据两个分片。
    3. 替换 `subprocess.Popen`，记录播放器收到的数据和控制消息。

    预期结果：
    1. SDK 会给播放器命令补齐 `-i -`。
    2. 服务端音频分片会直接写入播放器 stdin，不生成临时持久文件。
    3. 设备在写入首段音频后回报 `actuator.audio.started`，结束后回报 `actuator.audio.finished`。
    """

    app_root = tmp_path / "openaiglass-for-blind"
    config_dir = app_root / "host/glass-playback/config"
    audio_path = app_root / "testdata/audio/trigger.wav"
    config_dir.mkdir(parents=True)
    _write_wav(audio_path)
    config_path = config_dir / "glass.audio_stream_play.json"
    config_path.write_text(
        json.dumps(
            {
                "device_id": "glass-playback-001",
                "pair_token": "pair-playback",
                "control_ws_url": "ws://127.0.0.1:8765/ws/control",
                "startup": {"wait_for_binding": False},
                "sensors": {
                    "trigger_audio": {
                        "path": "testdata/audio/trigger.wav",
                        "sample_rate_hz": 16000,
                        "channels": 1,
                        "chunk_ms": 20,
                    }
                },
                "actuators": {
                    "audio_play": {
                        "mode": "play_and_auto_finish",
                        "player_command": "ffplay -nodisp -autoexit -loglevel error",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    class _ChunkedAudioResponse:
        def __init__(self) -> None:
            self._chunks = [b"RIFF" + b"\x00" * 40, b"audio-pcm", b""]

        def __enter__(self) -> "_ChunkedAudioResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self, _size: int) -> bytes:
            return self._chunks.pop(0)

    class _FakeStdin:
        def __init__(self) -> None:
            self.data = bytearray()
            self.closed = False

        def write(self, chunk: bytes) -> None:
            self.data.extend(chunk)

        def flush(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    class _FakeProcess:
        def __init__(self, command: list[str]) -> None:
            self.command = command
            self.stdin = _FakeStdin()
            self.killed = False

        def wait(self) -> int:
            return 0

        def kill(self) -> None:
            self.killed = True

    processes: list[_FakeProcess] = []

    def _fake_urlopen(url: str, *, timeout: float) -> _ChunkedAudioResponse:
        assert "/stream.wav" in url
        assert timeout > 0
        return _ChunkedAudioResponse()

    def _fake_popen(command: list[str], *, stdin) -> _FakeProcess:
        assert stdin is not None
        process = _FakeProcess(command)
        processes.append(process)
        return process

    monkeypatch.setattr("openaiglass_glass_playback.glass_device.urlopen", _fake_urlopen)
    monkeypatch.setattr("openaiglass_glass_playback.glass_device.subprocess.Popen", _fake_popen)

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
    device._join_audio_save_threads(timeout_seconds=1)  # noqa: SLF001

    assert processes
    assert processes[0].command[-4:] == ["-f", "wav", "-i", "-"]
    assert "-fflags" in processes[0].command
    assert "-analyzeduration" in processes[0].command
    assert bytes(processes[0].stdin.data) == b"RIFF" + b"\x00" * 40 + b"audio-pcm"
    assert processes[0].stdin.closed is True
    sent_names = [json.loads(text)["name"] for text in control.sent_texts]
    assert sent_names == ["actuator.audio.started", "actuator.audio.finished"]
    assert not (app_root / "runs/playback/glass-playback-001/audio/stream_audio_001.wav").exists()


def test_playback_warns_when_player_command_is_ignored(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """测试目标：配置播放器命令但未启用播放模式时给出明确提示。

    测试方法：
    1. 配置 `audio_play.mode=record_and_auto_finish` 和 `player_command`。
    2. 处理一次 `actuator.audio.play` 控制消息。
    3. 读取命令行输出。

    预期结果：
    1. 输出包含 `player_command 被忽略`。
    2. 提示开发者把 mode 改为 `play_and_auto_finish`。
    """

    app_root = tmp_path / "openaiglass-for-blind"
    config_dir = app_root / "host/glass-playback/config"
    audio_path = app_root / "testdata/audio/trigger.wav"
    config_dir.mkdir(parents=True)
    _write_wav(audio_path)
    config_path = config_dir / "glass.audio_player_ignored.json"
    config_path.write_text(
        json.dumps(
            {
                "device_id": "glass-playback-001",
                "pair_token": "pair-playback",
                "control_ws_url": "ws://127.0.0.1:8765/ws/control",
                "startup": {"wait_for_binding": False},
                "sensors": {
                    "trigger_audio": {
                        "path": "testdata/audio/trigger.wav",
                        "sample_rate_hz": 16000,
                        "channels": 1,
                        "chunk_ms": 20,
                    }
                },
                "actuators": {
                    "audio_play": {
                        "mode": "record_and_auto_finish",
                        "player_command": "ffplay -nodisp -autoexit",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

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

    output = capsys.readouterr().out
    assert "audio_play.player_command 被忽略" in output
    assert "play_and_auto_finish" in output


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
