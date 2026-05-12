from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_DEVICE = {
    "user_id": "user-system-test",
    "device_id": "dev-python-playback-glass",
    "name": "Python 回放眼镜",
    "device_name": "python-playback-glass",
    "client_type": "python-playback-glass",
    "sdk_version": "audio-chat-python-playback-glass-0.1.0",
    "supports": {
        "sensors": [{"type": "rgb", "modes": ["single", "continuous"], "default": {"format": "jpeg", "frequency_hz": 1, "sample_count": 1}}],
        "actuators": [{"type": "vibrator", "commands": ["vibrate"]}],
    },
    "properties": {
        "audio.input.sample_rate": 16000,
        "audio.input.channels": 1,
        "audio.input.chunk_ms": 20,
        "audio_chat.audio_input": "sensor.mic",
        "audio_chat.audio_output": "actuator.speaker",
        "debug.playback": True,
    },
}


@dataclass(frozen=True)
class PlaybackCase:
    """描述单个系统回放 Case。"""

    id: str
    name: str
    path: Path
    description: str = ""
    source: dict[str, Any] = field(default_factory=dict)
    device: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)
    playback: dict[str, Any] = field(default_factory=dict)

    @property
    def timeout_seconds(self) -> float:
        """返回本 Case 的回放超时时间。"""

        return float(self.playback.get("timeout_seconds") or 30)


@dataclass(frozen=True)
class PlaybackSuite:
    """描述一组系统回放 Case。"""

    id: str
    name: str
    path: Path
    cases: list[Path]
    defaults: dict[str, Any] = field(default_factory=dict)


def load_case(path: str | Path) -> PlaybackCase:
    """加载单个 Case YAML。"""

    case_path = Path(path).expanduser().resolve()
    raw = _read_yaml(case_path)
    case_id = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not case_id:
        raise ValueError(f"case id is required: {case_path}")
    if not name:
        raise ValueError(f"case name is required: {case_path}")
    return PlaybackCase(
        id=case_id,
        name=name,
        path=case_path,
        description=str(raw.get("description") or ""),
        source=dict(raw.get("source") or {}),
        device=_merge_device(raw.get("device")),
        inputs=dict(raw.get("inputs") or {}),
        expect=dict(raw.get("expect") or {}),
        playback=dict(raw.get("playback") or {}),
    )


def load_suite(path: str | Path) -> PlaybackSuite:
    """加载 suite YAML。"""

    suite_path = Path(path).expanduser().resolve()
    raw = _read_yaml(suite_path)
    case_paths = []
    for item in raw.get("cases") or []:
        case_path = Path(str(item)).expanduser()
        if not case_path.is_absolute():
            case_path = (suite_path.parent / case_path).resolve()
            if not case_path.exists():
                case_path = (Path.cwd() / str(item)).resolve()
        case_paths.append(case_path)
    if not case_paths:
        raise ValueError(f"suite cases are required: {suite_path}")
    return PlaybackSuite(
        id=str(raw.get("id") or "").strip(),
        name=str(raw.get("name") or raw.get("id") or "").strip(),
        path=suite_path,
        cases=case_paths,
        defaults=dict(raw.get("defaults") or {}),
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    """读取 YAML 文件并返回字典。"""

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def _merge_device(raw: dict[str, Any] | None) -> dict[str, Any]:
    """合并默认设备声明和 Case 设备声明。"""

    merged = {**DEFAULT_DEVICE, "supports": dict(DEFAULT_DEVICE["supports"]), "properties": dict(DEFAULT_DEVICE["properties"])}
    for key, value in dict(raw or {}).items():
        if key == "properties":
            merged["properties"] = {**merged["properties"], **dict(value or {})}
        elif key == "supports":
            merged["supports"] = dict(value or {})
        else:
            merged[key] = value
    return merged
