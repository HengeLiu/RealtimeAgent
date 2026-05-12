from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .case_schema import PlaybackCase
from .protocol_client import PlaybackStats


@dataclass(frozen=True)
class FailedAssertion:
    """单条失败断言。"""

    path: str
    expected: Any
    actual: Any
    message: str


@dataclass(frozen=True)
class AssertionResult:
    """Case 断言结果。"""

    ok: bool
    failed_assertions: list[FailedAssertion] = field(default_factory=list)
    runs_dir: str = ""
    summary: dict[str, Any] = field(default_factory=dict)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL 文件；不存在时返回空列表。"""

    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_model_request(path: Path) -> dict[str, Any]:
    """读取 model-request.json；不存在时返回空字典。"""

    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def find_case_runs_dir(runs_root: str | Path | None, stats: PlaybackStats) -> Path | None:
    """根据回放统计从 runs_root 中定位 session 目录。"""

    if runs_root is None:
        return None
    root = Path(runs_root).expanduser().resolve()
    if stats.session_id and (root / "sessions" / stats.session_id).exists():
        return root / "sessions" / stats.session_id
    if stats.session_id:
        for user_dir in root.iterdir() if root.exists() else []:
            candidate = user_dir / stats.session_id
            if candidate.exists() and candidate.is_dir():
                return candidate
    sessions_root = root / "sessions"
    if not sessions_root.exists():
        return None
    sessions = sorted((item for item in sessions_root.iterdir() if item.is_dir()), key=lambda item: item.stat().st_mtime, reverse=True)
    return sessions[0] if sessions else None


def assert_case(case: PlaybackCase, *, runs_root: str | Path | None, stats: PlaybackStats) -> AssertionResult:
    """执行系统级 runs 产物断言。"""

    runs_dir = find_case_runs_dir(runs_root, stats)
    artifacts = _load_artifacts(runs_dir)
    failures: list[FailedAssertion] = []
    expect = case.expect or {}
    _assert_includes(failures, "events.includes", (expect.get("events") or {}).get("includes") or [], _event_names(artifacts, stats))
    _assert_includes(failures, "streams.includes", (expect.get("streams") or {}).get("includes") or [], _stream_types(artifacts, stats))
    _assert_includes(failures, "tools.called", (expect.get("tools") or {}).get("called") or [], _tool_names(artifacts))
    _assert_includes(failures, "tasks.signals.includes", ((expect.get("tasks") or {}).get("signals") or {}).get("includes") or [], _task_signals(artifacts))
    for stream_type, config in (expect.get("assets") or {}).items():
        actual = sum(1 for item in artifacts["assets"] if item.get("stream_type") == stream_type)
        minimum = int((config or {}).get("min_count") or 0)
        if actual < minimum:
            failures.append(FailedAssertion(f"assets.{stream_type}.min_count", minimum, actual, f"{stream_type} asset count is too low"))
    min_audio_chunks = int((expect.get("output") or {}).get("min_audio_chunks") or 0)
    if len(stats.output_chunks) < min_audio_chunks:
        failures.append(FailedAssertion("output.min_audio_chunks", min_audio_chunks, len(stats.output_chunks), "speaker output chunks are too few"))
    if (expect.get("errors") or {}).get("disallow_system_error") and artifacts["system_events"]:
        failures.append(FailedAssertion("errors.disallow_system_error", False, True, f"system errors found: {len(artifacts['system_events'])}"))
    _assert_includes(failures, "model_request.tools.includes", ((expect.get("model_request") or {}).get("tools") or {}).get("includes") or [], _model_tool_names(artifacts))
    return AssertionResult(
        ok=not failures,
        failed_assertions=failures,
        runs_dir=str(runs_dir) if runs_dir else "",
        summary={"streams": sorted(_stream_types(artifacts, stats)), "tools": sorted(_tool_names(artifacts)), "output_chunks": len(stats.output_chunks)},
    )


def _load_artifacts(runs_dir: Path | None) -> dict[str, Any]:
    """读取断言需要的 runs 产物。"""

    if runs_dir is None:
        return {"events": [], "stream_events": [], "tool_events": [], "task_signals": [], "assets": [], "system_events": [], "model_request": {}}
    root = runs_dir.parents[1] if runs_dir.parent.name == "sessions" else runs_dir.parent
    return {
        "events": read_jsonl(runs_dir / "events.jsonl"),
        "stream_events": read_jsonl(runs_dir / "stream-events.jsonl"),
        "tool_events": read_jsonl(runs_dir / "tool-events.jsonl"),
        "task_signals": read_jsonl(runs_dir / "task-signals.jsonl"),
        "assets": read_jsonl(runs_dir / "assets.jsonl"),
        "system_events": read_jsonl(root / "system-events.jsonl"),
        "model_request": load_model_request(runs_dir / "model-request.json"),
    }


def _event_names(artifacts: dict[str, Any], stats: PlaybackStats) -> set[str]:
    """汇总控制事件名。"""

    return {str(item.get("event_name")) for item in artifacts["events"] + stats.received_events + stats.sent_events if item.get("event_name")}


def _stream_types(artifacts: dict[str, Any], stats: PlaybackStats) -> set[str]:
    """汇总 stream 类型。"""

    result = {str(item.get("stream_type")) for item in artifacts["stream_events"] if item.get("stream_type")}
    result.update(str(item.get("stream_type")) for item in stats.output_chunks if item.get("stream_type"))
    result.update(str(item.get("stream_type")) for item in stats.input_streams.values() if item.get("stream_type"))
    result.update(str(item.get("stream_type")) for item in stats.asset_uploads if item.get("stream_type"))
    return result


def _tool_names(artifacts: dict[str, Any]) -> set[str]:
    """从 tool-events.jsonl 中提取工具名。"""

    return {str(item.get("tool_name") or item.get("name") or item.get("function_name")) for item in artifacts["tool_events"] if item.get("tool_name") or item.get("name") or item.get("function_name")}


def _task_signals(artifacts: dict[str, Any]) -> set[str]:
    """从 task-signals.jsonl 中提取任务信号名。"""

    return {str(item.get("signal") or item.get("event_name") or item.get("task_type")) for item in artifacts["task_signals"] if item}


def _model_tool_names(artifacts: dict[str, Any]) -> set[str]:
    """从 model-request.json 中提取工具名。"""

    names = set()
    for item in (artifacts["model_request"] or {}).get("tools") or []:
        if isinstance(item, dict):
            function = item.get("function") if isinstance(item.get("function"), dict) else {}
            name = item.get("name") or function.get("name")
            if name:
                names.add(str(name))
    return names


def _assert_includes(failures: list[FailedAssertion], path: str, expected: list[str], actual: set[str]) -> None:
    """检查集合包含关系。"""

    for item in expected:
        if item not in actual:
            failures.append(FailedAssertion(path, item, sorted(actual), f"missing expected item: {item}"))
