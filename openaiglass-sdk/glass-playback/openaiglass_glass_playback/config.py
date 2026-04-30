"""`glass-playback` 配置解析与校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


@dataclass(slots=True)
class StartupConfig:
    """虚拟设备启动等待策略。"""

    wait_for_registration: bool = True
    wait_for_binding: bool = True
    wait_for_voice_session: bool = True
    auto_stream_trigger_audio: bool = True
    startup_timeout_ms: int = 30000


@dataclass(slots=True)
class TriggerAudioConfig:
    """触发音频配置。

    主要属性：
    1. `source`：音频来源，`file` 表示 WAV 文件，`microphone` 表示本机真实麦克风。
    2. `path`：文件来源时的 WAV 路径。
    3. `duration_ms`：麦克风来源时的固定录音时长。
    """

    source: str = "file"
    path: Path | None = None
    format: str = "wav"
    sample_rate_hz: int = 16000
    channels: int = 1
    chunk_ms: int = 40
    duration_ms: int = 5000
    microphone_device: object | None = None


@dataclass(slots=True)
class OutputConfig:
    """设备级回放输出文件配置。"""

    event_log: Path
    actuator_log: Path


@dataclass(slots=True)
class ServerArtifactCheck:
    """服务端业务产物断言配置。"""

    path: Path
    min_size_bytes: int = 1
    label: str = ""


@dataclass(slots=True)
class AssertionConfig:
    """设备级回放断言配置。"""

    server_artifacts: list[ServerArtifactCheck] = field(default_factory=list)


@dataclass(slots=True)
class PlaybackConfig:
    """`glass-playback` 设备配置。"""

    config_path: Path
    device_type: str
    device_id: str
    pair_token: str
    control_ws_url: str
    audio_ws_url: str
    desired_phone_device_id: str | None
    startup: StartupConfig
    trigger_audio: TriggerAudioConfig
    sensors: dict[str, Any] = field(default_factory=dict)
    actuators: dict[str, Any] = field(default_factory=dict)
    outputs: OutputConfig | None = None
    assertions: AssertionConfig = field(default_factory=AssertionConfig)
    trigger_audio_sequence: list[TriggerAudioConfig] = field(default_factory=list)
    trigger_audio_sequence_enabled: bool = False

    @classmethod
    def load(cls, path: str | Path, *, repo_root: str | Path = ".") -> "PlaybackConfig":
        """从 JSON 文件加载配置。"""

        config_path = Path(path).resolve()
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("glass-playback 配置必须是 JSON object")

        repo = Path(repo_root).resolve()
        app_root = _find_app_root(config_path) or repo / "openaiglass-for-blind"

        device_type = str(raw.get("device_type") or "glass").strip()
        if device_type != "glass":
            raise ValueError("glass-playback 仅支持 device_type=glass")

        device_id = str(raw.get("device_id") or "").strip()
        pair_token = str(raw.get("pair_token") or "").strip()
        control_ws_url = str(raw.get("control_ws_url") or "").strip()
        if not device_id:
            raise ValueError("glass-playback 配置缺少 device_id")
        if not pair_token:
            raise ValueError("glass-playback 配置缺少 pair_token")
        if not control_ws_url:
            raise ValueError("glass-playback 配置缺少 control_ws_url")
        _validate_ws_url(control_ws_url, field_name="control_ws_url")

        audio_ws_url = str(raw.get("audio_ws_url") or "").strip() or _derive_audio_ws_url(control_ws_url)
        _validate_ws_url(audio_ws_url, field_name="audio_ws_url")

        startup = _load_startup(raw.get("startup"))
        sensors = raw.get("sensors")
        if not isinstance(sensors, dict):
            raise ValueError("glass-playback 配置缺少 sensors")
        trigger_raw = sensors.get("trigger_audio")
        if not isinstance(trigger_raw, dict):
            raise ValueError("glass-playback 配置必须包含 sensors.trigger_audio")
        _validate_optional_sensor_assets(sensors, config_path=config_path, app_root=app_root, repo_root=repo)
        trigger_audio = _load_trigger_audio(
            trigger_raw,
            config_path=config_path,
            app_root=app_root,
            repo_root=repo,
        )
        trigger_audio_sequence = _load_trigger_audio_sequence(
            sensors.get("trigger_audio_sequence"),
            fallback=trigger_audio,
            config_path=config_path,
            app_root=app_root,
            repo_root=repo,
        )

        actuators = raw.get("actuators")
        if not isinstance(actuators, dict):
            actuators = {}

        outputs = _load_outputs(
            raw.get("outputs"),
            config_path=config_path,
            app_root=app_root,
            device_id=device_id,
        )
        assertions = _load_assertions(
            raw.get("assertions"),
            config_path=config_path,
            app_root=app_root,
        )

        return cls(
            config_path=config_path,
            device_type=device_type,
            device_id=device_id,
            pair_token=pair_token,
            control_ws_url=control_ws_url,
            audio_ws_url=audio_ws_url,
            desired_phone_device_id=_optional_string(raw.get("desired_phone_device_id")),
            startup=startup,
            trigger_audio=trigger_audio,
            sensors=sensors,
            actuators=actuators,
            outputs=outputs,
            assertions=assertions,
            trigger_audio_sequence=trigger_audio_sequence,
            trigger_audio_sequence_enabled=sensors.get("trigger_audio_sequence") is not None,
        )


def _optional_string(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _load_startup(raw: object) -> StartupConfig:
    data = raw if isinstance(raw, dict) else {}
    return StartupConfig(
        wait_for_registration=bool(data.get("wait_for_registration", True)),
        wait_for_binding=bool(data.get("wait_for_binding", True)),
        wait_for_voice_session=bool(data.get("wait_for_voice_session", True)),
        auto_stream_trigger_audio=bool(data.get("auto_stream_trigger_audio", True)),
        startup_timeout_ms=int(data.get("startup_timeout_ms", 30000)),
    )


def _load_trigger_audio(
    raw: dict[str, Any],
    *,
    config_path: Path,
    app_root: Path,
    repo_root: Path,
) -> TriggerAudioConfig:
    source = str(raw.get("source") or "file").strip().lower()
    if source not in {"file", "microphone"}:
        raise ValueError("sensors.trigger_audio.source 必须是 file 或 microphone")
    audio_path: Path | None = None
    fmt = str(raw.get("format") or "wav").lower()
    if source == "file":
        audio_path = _resolve_path(
            raw.get("path"),
            config_path=config_path,
            app_root=app_root,
            repo_root=repo_root,
            field_name="sensors.trigger_audio.path",
        )
        fmt = str(raw.get("format") or audio_path.suffix.lstrip(".") or "wav").lower()
        if fmt != "wav":
            raise ValueError("trigger_audio 文件来源当前仅支持 WAV")
    else:
        fmt = str(raw.get("format") or "pcm16").lower()
        if fmt not in {"pcm16", "pcm16le"}:
            raise ValueError("trigger_audio 麦克风来源当前仅支持 pcm16")
    return TriggerAudioConfig(
        source=source,
        path=audio_path,
        format=fmt,
        sample_rate_hz=int(raw.get("sample_rate_hz", 16000)),
        channels=int(raw.get("channels", 1)),
        chunk_ms=int(raw.get("chunk_ms", 40)),
        duration_ms=max(int(raw.get("duration_ms", 5000)), 1),
        microphone_device=raw.get("device"),
    )


def _load_trigger_audio_sequence(
    raw: object,
    *,
    fallback: TriggerAudioConfig,
    config_path: Path,
    app_root: Path,
    repo_root: Path,
) -> list[TriggerAudioConfig]:
    """加载连续语音回放队列。

    主要逻辑：
    1. 未配置 `sensors.trigger_audio_sequence` 时，使用单条 `trigger_audio` 保持旧行为。
    2. 配置后按数组顺序加载每一条触发音频。

    参数：
    1. `raw`：配置文件中的 `sensors.trigger_audio_sequence`。
    2. `fallback`：旧版单条触发音频配置。
    3. `config_path/app_root/repo_root`：路径解析上下文。

    返回值：
    1. 按提交顺序排列的触发音频配置列表。

    异常情况：
    1. 字段不是数组、数组为空或元素不是 JSON object 时抛出 `ValueError`。
    """

    if raw is None:
        return [fallback]
    if not isinstance(raw, list):
        raise ValueError("sensors.trigger_audio_sequence 必须是数组")
    if not raw:
        raise ValueError("sensors.trigger_audio_sequence 不能为空")
    sequence: list[TriggerAudioConfig] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"sensors.trigger_audio_sequence[{index}] 必须是 JSON object")
        sequence.append(
            _load_trigger_audio(
                item,
                config_path=config_path,
                app_root=app_root,
                repo_root=repo_root,
            )
        )
    return sequence


def _load_outputs(raw: object, *, config_path: Path, app_root: Path, device_id: str) -> OutputConfig:
    data = raw if isinstance(raw, dict) else {}
    default_root = app_root / "runs/playback" / device_id
    event_log = _resolve_output_path(
        data.get("event_log") or default_root / "events.jsonl",
        config_path=config_path,
        app_root=app_root,
    )
    actuator_log = _resolve_output_path(
        data.get("actuator_log") or default_root / "actuators.jsonl",
        config_path=config_path,
        app_root=app_root,
    )
    return OutputConfig(event_log=event_log, actuator_log=actuator_log)


def _load_assertions(raw: object, *, config_path: Path, app_root: Path) -> AssertionConfig:
    """加载设备级回放断言配置。

    主要逻辑：
    1. 当前先支持服务端业务产物文件断言。
    2. 相对路径按输出文件相同规则解析，`runs/` 开头时相对业务工程根目录。
    3. 产物路径允许保留 `{session_id}`、`{device_id}` 占位符，由运行时替换。

    参数：
    1. `raw`：配置文件中的 `assertions` 字段。
    2. `config_path`：当前配置文件路径。
    3. `app_root`：业务工程根目录。

    返回值：
    1. `AssertionConfig`：设备级断言配置。

    异常情况：
    1. `server_artifacts` 不是数组、数组项不是对象或缺少 path 时抛出 `ValueError`。
    """

    data = raw if isinstance(raw, dict) else {}
    artifacts_raw = data.get("server_artifacts", [])
    if artifacts_raw is None:
        artifacts_raw = []
    if not isinstance(artifacts_raw, list):
        raise ValueError("assertions.server_artifacts 必须是数组")

    artifacts: list[ServerArtifactCheck] = []
    for index, item in enumerate(artifacts_raw):
        if not isinstance(item, dict):
            raise ValueError(f"assertions.server_artifacts[{index}] 必须是 JSON object")
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            raise ValueError(f"assertions.server_artifacts[{index}].path 不能为空")
        artifacts.append(
            ServerArtifactCheck(
                path=_resolve_output_path(raw_path, config_path=config_path, app_root=app_root),
                min_size_bytes=max(int(item.get("min_size_bytes", 1)), 0),
                label=str(item.get("label") or raw_path),
            )
        )
    return AssertionConfig(server_artifacts=artifacts)


def _validate_optional_sensor_assets(
    sensors: dict[str, Any],
    *,
    config_path: Path,
    app_root: Path,
    repo_root: Path,
) -> None:
    """校验可选传感器资产路径。

    参数：
    1. `sensors`：配置中的传感器段。
    2. `config_path`：配置文件路径。
    3. `app_root`：业务工程根目录。
    4. `repo_root`：仓库根目录。

    异常情况：
    1. 可选资产配置了路径但文件不存在时抛出异常。
    """

    camera_capture = sensors.get("camera_capture")
    if isinstance(camera_capture, dict) and camera_capture.get("path"):
        _resolve_path(
            camera_capture.get("path"),
            config_path=config_path,
            app_root=app_root,
            repo_root=repo_root,
            field_name="sensors.camera_capture.path",
        )

    camera_stream = sensors.get("camera_stream")
    if isinstance(camera_stream, dict):
        if camera_stream.get("path"):
            _resolve_path(
                camera_stream.get("path"),
                config_path=config_path,
                app_root=app_root,
                repo_root=repo_root,
                field_name="sensors.camera_stream.path",
            )
        frames = camera_stream.get("frames")
        if isinstance(frames, list):
            for index, item in enumerate(frames):
                if not isinstance(item, dict):
                    raise ValueError(f"sensors.camera_stream.frames[{index}] 必须是 JSON object")
                _resolve_path(
                    item.get("path"),
                    config_path=config_path,
                    app_root=app_root,
                    repo_root=repo_root,
                    field_name=f"sensors.camera_stream.frames[{index}].path",
                )

    heading = sensors.get("heading")
    if isinstance(heading, dict) and heading.get("path"):
        _resolve_path(
            heading.get("path"),
            config_path=config_path,
            app_root=app_root,
            repo_root=repo_root,
            field_name="sensors.heading.path",
        )


def _resolve_path(
    value: object,
    *,
    config_path: Path,
    app_root: Path,
    repo_root: Path,
    field_name: str,
) -> Path:
    raw_path = str(value or "").strip()
    if not raw_path:
        raise ValueError(f"glass-playback 配置缺少 {field_name}")
    path = Path(raw_path)
    candidates = [path] if path.is_absolute() else [app_root / path, repo_root / path, config_path.parent / path]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    raise FileNotFoundError(f"找不到 {field_name}: {raw_path}")


def _resolve_output_path(value: object, *, config_path: Path, app_root: Path) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    if str(value).startswith("runs/"):
        return (app_root / path).resolve()
    return (config_path.parent / path).resolve()


def _find_app_root(path: Path) -> Path | None:
    for parent in [path.parent, *path.parents]:
        if parent.name == "openaiglass-for-blind":
            return parent
    return None


def _validate_ws_url(value: str, *, field_name: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise ValueError(f"{field_name} 必须是 ws:// 或 wss:// 地址")


def _derive_audio_ws_url(control_ws_url: str) -> str:
    parsed = urlsplit(control_ws_url)
    netloc = parsed.netloc
    return f"{parsed.scheme}://{netloc}/ws_audio"
