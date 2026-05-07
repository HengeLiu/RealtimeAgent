from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from urllib.request import urlopen

from audio_chat.config import AudioChatYamlConfig, load_yaml_config
from audio_chat.protocol import CONTROL_EVENTS, STREAM_TYPES


def main(argv: list[str] | None = None) -> None:
    """运行 audio-chat 本地预检。

    主要逻辑：
    1. 读取 YAML 配置和 dev_checks。
    2. 聚合协议契约、包导入、边界、live server 和最近回放检查。
    3. 输出稳定 JSON 报告，供 P0 验收和后续并行开发复用。

    参数：`argv` 为命令行参数。
    返回值：无。
    异常情况：必需检查失败时以非零退出码结束。
    """

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="")
    parser.add_argument("--require-server", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)

    loaded = load_yaml_config(args.config) if args.config else AudioChatYamlConfig()
    report_path = Path(args.report or loaded.dev_checks.report_path)
    checks = []
    checks.append(_protocol_summary_check())
    if loaded.dev_checks.run_contract_tests:
        checks.append(_contract_tests_check(loaded.dev_checks.contract_tests_path))
    else:
        checks.append(_skipped("contract_tests", "disabled by dev_checks.run_contract_tests"))
    if loaded.dev_checks.run_package_check:
        checks.append(_package_import_check())
    else:
        checks.append(_skipped("package_import", "disabled by dev_checks.run_package_check"))
    if loaded.dev_checks.run_boundary_check:
        checks.append(_boundary_check())
    else:
        checks.append(_skipped("boundary", "disabled by dev_checks.run_boundary_check"))
    if args.require_server:
        checks.append(_live_server_check(loaded))
    else:
        checks.append(_skipped("live_server", "use --require-server to enable"))
    if loaded.dev_checks.require_recent_playback_ok:
        checks.append(_recent_playback_check())
    else:
        checks.append(_skipped("recent_playback", "disabled by dev_checks.require_recent_playback_ok"))

    ok = all(check["ok"] for check in checks)
    report = {
        "status": "ok" if ok else "failed",
        "ok": ok,
        "protocol_version": "audio-chat.v1",
        "control_events": sorted(CONTROL_EVENTS),
        "stream_types": sorted(STREAM_TYPES),
        "checks": checks,
        "not_implemented": {
            "audio_pipeline.resample": "loaded but not implemented beyond format validation",
            "audio_pipeline.volume_normalize": "loaded but not implemented",
            "audio_pipeline.vad": "TextAgentCore owns turn boundary; server VAD is not implemented",
            "audio_pipeline.asr_sidecar": "not implemented",
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"preflight {'ok' if ok else 'failed'}: {report_path}")
    if not ok:
        raise SystemExit(1)


def _protocol_summary_check() -> dict:
    """检查协议事件和 stream 类型基础集合。"""

    required_events = {
        "control.device.register.requested",
        "control.device.registered",
        "stream.input.opened",
        "stream.output.open.requested",
    }
    required_streams = {"sensor.mic", "actuator.speaker"}
    missing_events = sorted(required_events - set(CONTROL_EVENTS))
    missing_streams = sorted(required_streams - set(STREAM_TYPES))
    return {
        "name": "protocol_summary",
        "ok": not missing_events and not missing_streams,
        "missing_events": missing_events,
        "missing_streams": missing_streams,
    }


def _contract_tests_check(raw_path: str) -> dict:
    """检查 golden 契约文件是否存在且可解析。

    参数：`raw_path` 为配置中的契约目录，允许从仓库根或 audio-chat 目录运行。
    返回值：结构化检查结果。
    异常情况：本函数捕获解析错误并写入结果。
    """

    root = _resolve_audio_chat_path(raw_path)
    required_dirs = ["events", "streams", "scenarios"]
    errors: list[str] = []
    files: list[str] = []
    for directory_name in required_dirs:
        directory = root / directory_name
        if not directory.is_dir():
            errors.append(f"missing directory: {directory}")
            continue
        json_files = sorted(directory.glob("*.json"))
        if not json_files:
            errors.append(f"missing json golden: {directory}")
            continue
        for path in json_files:
            try:
                json.loads(path.read_text(encoding="utf-8"))
                files.append(str(path))
            except Exception as exc:
                errors.append(f"{path}: {type(exc).__name__}: {exc}")
    return {"name": "contract_tests", "ok": not errors, "root": str(root), "files": files, "errors": errors}


def _package_import_check() -> dict:
    """检查公开包和 P0 对象能稳定导入。"""

    required_names = [
        "ArtifactRef",
        "AssetRef",
        "AudioChatApp",
        "AudioChatConfig",
        "AudioChatError",
        "BaseTask",
        "BaseTool",
        "TaskEvent",
        "TaskRef",
        "ToolError",
        "ToolResult",
        "UserDeviceContext",
    ]
    errors: list[str] = []
    try:
        package = importlib.import_module("audio_chat")
        missing = [name for name in required_names if not hasattr(package, name)]
        errors.extend(f"missing public export: {name}" for name in missing)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {"name": "package_import", "ok": not errors, "required_names": required_names, "errors": errors}


def _boundary_check() -> dict:
    """检查 server SDK 核心包没有把 endpoint 参考实现混进顶层公开 API。"""

    errors: list[str] = []
    try:
        package = importlib.import_module("audio_chat")
        forbidden_names = ["PythonPlaybackEndpoint", "Esp32AecEndpointState"]
        leaked = [name for name in forbidden_names if hasattr(package, name)]
        errors.extend(f"endpoint reference leaked from audio_chat: {name}" for name in leaked)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {"name": "boundary", "ok": not errors, "errors": errors}


def _live_server_check(config: AudioChatYamlConfig) -> dict:
    """检查已启动 server 的 health 和 debug devices。"""

    errors: list[str] = []
    server_health = None
    server_debug = None
    try:
        health_url = config.server.public_url.rstrip("/") + "/api/health"
        with urlopen(health_url, timeout=5) as response:
            server_health = json.loads(response.read().decode("utf-8"))
        debug_url = config.server.public_url.rstrip("/") + "/api/debug/devices"
        with urlopen(debug_url, timeout=5) as response:
            server_debug = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {
        "name": "live_server",
        "ok": not errors,
        "server_health": server_health,
        "server_debug_devices": server_debug,
        "errors": errors,
    }


def _recent_playback_check() -> dict:
    """检查最近一次 playback result 是否成功。

    主要逻辑：P0 只冻结检查入口，后续 playback 线路可以扩展 result schema。
    """

    roots = [Path("runs"), Path("audio-chat/runs")]
    candidates: list[Path] = []
    for root in roots:
        if root.exists():
            candidates.extend(root.rglob("result.json"))
    if not candidates:
        return {"name": "recent_playback", "ok": False, "errors": ["missing recent playback result.json"]}
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"name": "recent_playback", "ok": False, "path": str(latest), "errors": [f"{type(exc).__name__}: {exc}"]}
    ok = bool(data.get("ok") is True or data.get("status") == "ok")
    return {"name": "recent_playback", "ok": ok, "path": str(latest), "result": data, "errors": [] if ok else ["latest playback result is not ok"]}


def _skipped(name: str, reason: str) -> dict:
    return {"name": name, "ok": True, "skipped": True, "reason": reason}


def _resolve_audio_chat_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.exists():
        return path
    if path.parts and path.parts[0] == "audio-chat":
        trimmed = Path(*path.parts[1:])
        if trimmed.exists():
            return trimmed
    return path


if __name__ == "__main__":
    main()
