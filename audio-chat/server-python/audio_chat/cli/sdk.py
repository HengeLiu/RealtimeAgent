from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
import tempfile
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
    parser.add_argument("--skip-wheel-build", action="store_true", help="跳过 wheel 构建检查")
    parser.add_argument("--skip-editable-install", action="store_true", help="跳过临时环境 editable install 检查")
    args = parser.parse_args(argv)

    pyproject = _resolve_audio_root_path(args.pyproject)
    audio_root = pyproject.parent
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

    endpoint_leaks = [
        name
        for name in ("NetworkPythonPlaybackEndpoint", "PythonPhoneMockEndpoint", "AudioChatWebEndpoint")
        if hasattr(package, name)
    ]
    errors.extend(f"endpoint reference leaked from audio_chat: {name}" for name in endpoint_leaks)

    wheel_check = _wheel_build_check(audio_root) if not args.skip_wheel_build else _skipped("wheel_build", "disabled by --skip-wheel-build")
    editable_check = (
        _editable_install_check(audio_root, public_names)
        if not args.skip_editable_install
        else _skipped("editable_install", "disabled by --skip-editable-install")
    )
    errors.extend(wheel_check["errors"])
    errors.extend(editable_check["errors"])

    report = {
        "ok": not errors,
        "script_count": len(scripts),
        "public_names": public_names,
        "checks": {
            "entry_points": {"ok": not [error for error in errors if error.startswith("entry point")], "script_count": len(scripts)},
            "public_api": {"ok": not missing, "missing": missing},
            "boundary": {"ok": not endpoint_leaks, "endpoint_leaks": endpoint_leaks},
            "wheel_build": wheel_check,
            "editable_install": editable_check,
        },
        "errors": errors,
    }
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


def _wheel_build_check(audio_root: Path) -> dict:
    """构建 wheel 并返回检查结果。

    主要逻辑：优先使用当前环境中的 `uv build --wheel`，产物写入临时目录，避免污染仓库。
    参数：`audio_root` 为 audio-chat 项目根目录。
    返回值：结构化检查结果。
    异常情况：构建工具缺失或构建失败时记录错误，不向上抛出。
    """

    if shutil.which("uv") is None:
        return {"ok": False, "errors": ["uv executable not found"]}
    with tempfile.TemporaryDirectory(prefix="audio-chat-wheel-") as temp_dir:
        completed = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", temp_dir],
            cwd=audio_root,
            text=True,
            capture_output=True,
            check=False,
        )
        wheels = sorted(Path(temp_dir).glob("*.whl"))
        errors = []
        if completed.returncode != 0:
            errors.append(f"uv build failed: {completed.stderr.strip() or completed.stdout.strip()}")
        if not wheels:
            errors.append("wheel file was not produced")
        return {
            "ok": not errors,
            "wheel_count": len(wheels),
            "stdout_tail": completed.stdout.splitlines()[-20:],
            "stderr_tail": completed.stderr.splitlines()[-20:],
            "errors": errors,
        }


def _editable_install_check(audio_root: Path, public_names: list[str]) -> dict:
    """在临时 uv 环境中检查 editable install 和公开 API 导入。

    主要逻辑：使用 `uv run --isolated --with-editable <audio_root>` 启动干净进程，
    实际导入 `audio_chat` 并确认公开对象存在。
    参数：`audio_root` 为 audio-chat 项目根目录，`public_names` 为必须导出的对象。
    返回值：结构化检查结果。
    异常情况：命令失败时记录错误。
    """

    if shutil.which("uv") is None:
        return {"ok": False, "errors": ["uv executable not found"]}
    code = (
        "import audio_chat; "
        f"missing=[name for name in {public_names!r} if not hasattr(audio_chat, name)]; "
        "raise SystemExit(1 if missing else 0)"
    )
    completed = subprocess.run(
        ["uv", "run", "--isolated", "--with-editable", str(audio_root), "python", "-c", code],
        cwd=audio_root,
        text=True,
        capture_output=True,
        check=False,
    )
    errors = []
    if completed.returncode != 0:
        errors.append(f"editable install import failed: {completed.stderr.strip() or completed.stdout.strip()}")
    return {
        "ok": not errors,
        "stdout_tail": completed.stdout.splitlines()[-20:],
        "stderr_tail": completed.stderr.splitlines()[-20:],
        "errors": errors,
    }


def _skipped(name: str, reason: str) -> dict:
    return {"ok": True, "name": name, "skipped": True, "reason": reason, "errors": []}
