from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .assertions import load_model_request, read_jsonl
from .case_schema import DEFAULT_DEVICE


@dataclass(frozen=True)
class RecordOptions:
    """录制归纳参数。"""

    runs_root: Path
    user_id: str
    device_id: str
    out: Path
    session_id: str = ""
    audio: str = ""
    images: dict[str, str] | None = None
    case_id: str = ""
    name: str = ""


def record_case(options: RecordOptions) -> dict[str, Any]:
    """从 server runs 产物生成 Case YAML 草稿。"""

    session_dir = _find_session_dir(options.runs_root, options.session_id)
    artifacts = _load_session_artifacts(session_dir, options.runs_root)
    case_id = options.case_id or (Path(options.audio).stem.replace(" ", "_") if options.audio else f"recorded_{session_dir.name}")
    data = {
        "id": case_id,
        "name": options.name or case_id,
        "description": "由 browser-glass 手动运行产物生成的 python-playback-glass 草稿。",
        "source": {"recorded_from": "browser-glass", "recorded_at": datetime.now(timezone.utc).isoformat(), "original_user_id": options.user_id, "original_device_id": options.device_id, "session_id": session_dir.name},
        "device": {**DEFAULT_DEVICE, "user_id": "user-system-test", "device_id": "dev-python-playback-glass"},
        "inputs": _build_inputs(options),
        "expect": _build_expect(artifacts),
        "playback": {"timeout_seconds": 30},
    }
    options.out.parent.mkdir(parents=True, exist_ok=True)
    options.out.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return data


def _find_session_dir(runs_root: Path, session_id: str) -> Path:
    """定位用于录制的 session 目录。"""

    sessions_root = runs_root.expanduser().resolve() / "sessions"
    if session_id:
        candidate = sessions_root / session_id
        if not candidate.exists():
            raise FileNotFoundError(f"session not found: {candidate}")
        return candidate
    sessions = sorted((item for item in sessions_root.iterdir() if item.is_dir()), key=lambda item: item.stat().st_mtime, reverse=True)
    if not sessions:
        raise FileNotFoundError(f"no sessions under: {sessions_root}")
    return sessions[0]


def _load_session_artifacts(session_dir: Path, runs_root: Path) -> dict[str, Any]:
    """读取录制归纳需要的产物。"""

    return {"events": read_jsonl(session_dir / "events.jsonl"), "stream_events": read_jsonl(session_dir / "stream-events.jsonl"), "tool_events": read_jsonl(session_dir / "tool-events.jsonl"), "task_signals": read_jsonl(session_dir / "task-signals.jsonl"), "assets": read_jsonl(session_dir / "assets.jsonl"), "system_events": read_jsonl(runs_root / "system-events.jsonl"), "model_request": load_model_request(session_dir / "model-request.json")}


def _build_inputs(options: RecordOptions) -> dict[str, Any]:
    """生成 Case 输入段。"""

    inputs: dict[str, Any] = {}
    if options.audio:
        inputs["audio"] = {"path": options.audio, "mode": "realtime_chunks", "chunk_ms": 20}
    sensors = {stream_type: {"fixtures": [{"path": path, "codec": "jpeg"}]} for stream_type, path in (options.images or {}).items()}
    if sensors:
        inputs["sensors"] = sensors
    return inputs


def _build_expect(artifacts: dict[str, Any]) -> dict[str, Any]:
    """把一次运行归纳成稳定断言。"""

    stream_types = sorted({str(item.get("stream_type")) for item in artifacts["stream_events"] if item.get("stream_type")})
    expect: dict[str, Any] = {
        "events": {"includes": sorted({str(item.get("event_name")) for item in artifacts["events"] if str(item.get("event_name") or "").startswith(("control.device.", "control.audio_session.", "stream.control.", "stream.output."))})},
        "streams": {"includes": stream_types},
        "output": {"min_audio_chunks": 1 if "actuator.speaker" in stream_types else 0},
        "errors": {"disallow_system_error": not artifacts["system_events"]},
    }
    tools = sorted({str(item.get("tool_name") or item.get("name") or item.get("function_name")) for item in artifacts["tool_events"] if item.get("tool_name") or item.get("name") or item.get("function_name")})
    if tools:
        expect["tools"] = {"called": tools}
    asset_counts: dict[str, int] = {}
    for item in artifacts["assets"]:
        if item.get("stream_type"):
            asset_counts[str(item["stream_type"])] = asset_counts.get(str(item["stream_type"]), 0) + 1
    if asset_counts:
        expect["assets"] = {key: {"min_count": value} for key, value in sorted(asset_counts.items())}
    model_tools = sorted(_model_tool_names(artifacts["model_request"]))
    if model_tools:
        expect["model_request"] = {"tools": {"includes": model_tools}}
    return expect


def _model_tool_names(request: dict[str, Any]) -> set[str]:
    """提取模型请求工具名。"""

    names = set()
    for item in request.get("tools") or []:
        if isinstance(item, dict):
            function = item.get("function") if isinstance(item.get("function"), dict) else {}
            name = item.get("name") or function.get("name")
            if name:
                names.add(str(name))
    return names
