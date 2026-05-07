from __future__ import annotations

import argparse
import importlib
import json
import tomllib
from pathlib import Path


def package_check(argv: list[str] | None = None) -> None:
    """检查 audio-chat SDK 包公共面。

    主要逻辑：
    1. 校验 pyproject 中的 entry point 能导入。
    2. 校验 README 常用公开类能从 audio_chat 顶层导入。
    3. 可选写入 JSON 报告。
    """

    parser = argparse.ArgumentParser(prog="audio-chat.sdk.package-check", description="检查 audio-chat SDK 包")
    parser.add_argument("--pyproject", default="pyproject.toml")
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)

    pyproject = _resolve_audio_root_path(args.pyproject)
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    scripts = data.get("project", {}).get("scripts", {})
    errors: list[str] = []
    for name, target in sorted(scripts.items()):
        module_name, _, attr = str(target).partition(":")
        try:
            module = importlib.import_module(module_name)
            if attr and not callable(getattr(module, attr, None)):
                errors.append(f"entry point target is not callable: {name}={target}")
        except Exception as exc:
            errors.append(f"entry point import failed: {name}={target}: {type(exc).__name__}: {exc}")
    package = importlib.import_module("audio_chat")
    public_names = ["AudioChatApp", "AudioChatConfig", "BaseTool", "BaseTask", "ToolResult", "TaskEvent", "UserDeviceContext"]
    missing = [name for name in public_names if not hasattr(package, name)]
    errors.extend(f"missing public export: {name}" for name in missing)
    report = {"ok": not errors, "script_count": len(scripts), "public_names": public_names, "errors": errors}
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


def _resolve_audio_root_path(raw: str) -> Path:
    path = Path(raw)
    if path.exists():
        return path
    audio_root = Path(__file__).resolve().parents[3]
    candidate = audio_root / raw
    if candidate.exists():
        return candidate
    return path

