from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
import tempfile
import tomllib
import zipfile
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
    project = data.get("project", {})
    scripts = project.get("scripts", {})
    version = str(project.get("version", "")).strip()
    release_candidate = _release_candidate_check(version=version, audio_root=audio_root)
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
    endpoint_sources = _endpoint_source_check(audio_root)
    source_boundary = _source_boundary_check(audio_root)
    errors.extend(release_candidate["errors"])
    errors.extend(endpoint_sources["errors"])
    errors.extend(source_boundary["errors"])

    if args.skip_wheel_build:
        wheel_check = _skipped("wheel_build", "disabled by --skip-wheel-build")
        wheel_install_check = _skipped("wheel_install", "disabled by --skip-wheel-build")
        wheel_contents = _skipped("wheel_contents", "disabled by --skip-wheel-build")
    else:
        with tempfile.TemporaryDirectory(prefix="audio-chat-release-") as temp_dir:
            wheel_check = _wheel_build_check(audio_root, Path(temp_dir))
            wheel_path = Path(wheel_check.get("wheel_path", ""))
            if wheel_check["ok"] and wheel_path.exists():
                wheel_install_check = _wheel_install_check(wheel_path, public_names)
                wheel_contents = _wheel_contents_check(wheel_path)
            else:
                wheel_install_check = _skipped("wheel_install", "wheel build failed")
                wheel_contents = _skipped("wheel_contents", "wheel build failed")
    editable_check = (
        _editable_install_check(audio_root, public_names)
        if not args.skip_editable_install
        else _skipped("editable_install", "disabled by --skip-editable-install")
    )
    errors.extend(wheel_check["errors"])
    errors.extend(wheel_install_check["errors"])
    errors.extend(wheel_contents["errors"])
    errors.extend(editable_check["errors"])

    report = {
        "ok": not errors,
        "package": {
            "name": str(project.get("name", "")),
            "version": version,
            "release_candidate": release_candidate["ok"],
            "python": str(project.get("requires-python", "")),
        },
        "script_count": len(scripts),
        "public_names": public_names,
        "checks": {
            "entry_points": {"ok": not [error for error in errors if error.startswith("entry point")], "script_count": len(scripts)},
            "public_api": {"ok": not missing, "missing": missing},
            "boundary": {"ok": not endpoint_leaks, "endpoint_leaks": endpoint_leaks},
            "source_boundary": source_boundary,
            "endpoint_sources": endpoint_sources,
            "release_candidate": release_candidate,
            "wheel_build": wheel_check,
            "wheel_install": wheel_install_check,
            "wheel_contents": wheel_contents,
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


def _release_candidate_check(*, version: str, audio_root: Path) -> dict:
    """检查当前发布候选标识和变更记录。

    主要逻辑：版本号必须包含 PEP 440 rc 标识，CHANGELOG 必须记录当前版本、
    当前不兼容点和 package gate。这样 package-check 的报告可以直接作为
    release candidate 摘要交付。
    """

    errors = []
    changelog = audio_root / "CHANGELOG.md"
    if "rc" not in version:
        errors.append(f"project version must be a release candidate: {version}")
    if not changelog.exists():
        errors.append("missing CHANGELOG.md")
        changelog_text = ""
    else:
        changelog_text = changelog.read_text(encoding="utf-8")
        for expected in (version, "不兼容点", "package-check", "old-sdk-parity-release"):
            if expected not in changelog_text:
                errors.append(f"CHANGELOG.md missing release note: {expected}")
    return {
        "ok": not errors,
        "version": version,
        "changelog": str(changelog),
        "errors": errors,
    }


def _wheel_build_check(audio_root: Path, out_dir: Path) -> dict:
    """构建 wheel 并返回检查结果。

    主要逻辑：优先使用当前环境中的 `uv build --wheel`，产物写入临时目录，避免污染仓库。
    参数：`audio_root` 为 audio-chat 项目根目录。
    返回值：结构化检查结果。
    异常情况：构建工具缺失或构建失败时记录错误，不向上抛出。
    """

    if shutil.which("uv") is None:
        return {"ok": False, "errors": ["uv executable not found"]}
    out_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
        cwd=audio_root,
        text=True,
        capture_output=True,
        check=False,
    )
    wheels = sorted(out_dir.glob("*.whl"))
    errors = []
    if completed.returncode != 0:
        errors.append(f"uv build failed: {completed.stderr.strip() or completed.stdout.strip()}")
    if not wheels:
        errors.append("wheel file was not produced")
    return {
        "ok": not errors,
        "wheel_count": len(wheels),
        "wheel_path": str(wheels[0]) if wheels else "",
        "stdout_tail": completed.stdout.splitlines()[-20:],
        "stderr_tail": completed.stderr.splitlines()[-20:],
        "errors": errors,
    }


def _wheel_install_check(wheel_path: Path, public_names: list[str]) -> dict:
    """在隔离环境中安装 wheel 并验证公开 API。

    主要逻辑：使用刚构建出的 wheel，而不是源码 editable install，模拟业务项目
    安装发布产物后的导入行为。
    """

    if shutil.which("uv") is None:
        return {"ok": False, "errors": ["uv executable not found"]}
    code = (
        "import audio_chat; "
        f"missing=[name for name in {public_names!r} if not hasattr(audio_chat, name)]; "
        "raise SystemExit(1 if missing else 0)"
    )
    completed = subprocess.run(
        ["uv", "run", "--isolated", "--with", str(wheel_path), "python", "-c", code],
        cwd=wheel_path.parent,
        text=True,
        capture_output=True,
        check=False,
    )
    errors = []
    if completed.returncode != 0:
        errors.append(f"wheel install import failed: {completed.stderr.strip() or completed.stdout.strip()}")
    return {
        "ok": not errors,
        "wheel": str(wheel_path),
        "stdout_tail": completed.stdout.splitlines()[-20:],
        "stderr_tail": completed.stderr.splitlines()[-20:],
        "errors": errors,
    }


def _wheel_contents_check(wheel_path: Path) -> dict:
    """检查 wheel 内容和包数据边界。

    主要逻辑：确认类型标记 `py.typed` 进入包内，同时禁止本地配置、运行产物、
    缓存、端侧真机工程和 examples 被混入 server SDK wheel。
    """

    with zipfile.ZipFile(wheel_path) as wheel:
        names = sorted(wheel.namelist())
    forbidden_fragments = (
        ".venv/",
        ".pytest_cache/",
        "__pycache__/",
        ".pyc",
        "runs/",
        "examples/",
        "endpoints/ios-phone/",
        "endpoints/esp32-s3/",
        "endpoints/web-glass/",
        "local.env",
        "AppConfig.json",
    )
    forbidden = [
        name
        for name in names
        if any(fragment in name for fragment in forbidden_fragments)
    ]
    required = ["audio_chat/py.typed"]
    missing_required = [name for name in required if name not in names]
    errors = []
    if forbidden:
        errors.append(f"wheel contains forbidden files: {forbidden[:20]}")
    if missing_required:
        errors.append(f"wheel missing package data: {missing_required}")
    return {
        "ok": not errors,
        "wheel": str(wheel_path),
        "file_count": len(names),
        "required": required,
        "missing_required": missing_required,
        "forbidden": forbidden,
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


def _esp32_reference_check(audio_root: Path) -> dict:
    """检查 ESP32-S3 参考端随包输入。

    主要逻辑：package-check 不要求本机安装 ESP-IDF，也不假装真机构建成功；它只检查
    参考目录、README、本地 env 模板和 ESP-IDF 工程骨架是否齐全。
    """

    from audio_chat.cli.esp32 import _esp32_project_manifest_check

    endpoint_root = audio_root / "endpoints" / "esp32-s3"
    errors = []
    for relative in ("README.md", "local.env.example"):
        if not (endpoint_root / relative).exists():
            errors.append(f"missing ESP32 endpoint file: {relative}")
    firmware_check = _esp32_project_manifest_check(endpoint_root / "firmware")
    errors.extend(firmware_check["errors"])
    return {
        "ok": not errors,
        "endpoint_root": str(endpoint_root),
        "firmware": firmware_check,
        "errors": errors,
    }


def _endpoint_source_check(audio_root: Path) -> dict:
    """检查发布候选随仓库交付的端侧参考源码。

    主要逻辑：端侧参考实现不进入 Python wheel，但 release candidate 必须能说明
    iOS、ESP32、web-glass 和 Python phone mock 的源码输入是否齐全。
    """

    required_files = {
        "ios_phone": [
            "endpoints/ios-phone/README.md",
            "endpoints/ios-phone/AppConfig.example.json",
            "endpoints/ios-phone/AudioChatPhone.xcodeproj/project.pbxproj",
            "endpoints/ios-phone/AudioChatPhone/Core/AudioChatEndpointRuntime.swift",
        ],
        "esp32_s3": [
            "endpoints/esp32-s3/README.md",
            "endpoints/esp32-s3/local.env.example",
            "endpoints/esp32-s3/firmware/CMakeLists.txt",
        ],
        "web_glass": [
            "endpoints/web-glass/README.md",
            "endpoints/web-glass/index.html",
            "endpoints/web-glass/web-glass.yaml",
        ],
        "python_phone_mock": [
            "endpoints/python-phone-mock/README.md",
            "endpoints/python-phone-mock/phone.mock.yaml",
            "server-python/audio_chat/endpoints/python_phone_mock.py",
        ],
    }
    checks: dict[str, dict] = {}
    errors: list[str] = []
    for name, files in required_files.items():
        missing = [relative for relative in files if not (audio_root / relative).exists()]
        if missing:
            errors.extend(f"missing endpoint source file for {name}: {relative}" for relative in missing)
        checks[name] = {
            "ok": not missing,
            "required_files": files,
            "missing": missing,
        }
    esp32_check = _esp32_reference_check(audio_root)
    errors.extend(esp32_check["errors"])
    checks["esp32_s3"]["firmware"] = esp32_check["firmware"]
    return {"ok": not errors, "checks": checks, "errors": errors}


def _source_boundary_check(audio_root: Path) -> dict:
    """扫描 server SDK 核心源码，防止引用业务样例或端侧工程。

    主要逻辑：`audio_chat.cli` 和 `audio_chat.endpoints` 可以知道参考端侧位置；
    核心包、Tool / Task / Agent / Stream / Output 服务不能 import examples 或端侧工程。
    """

    core_root = audio_root / "server-python" / "audio_chat"
    allowed_prefixes = {
        core_root / "cli",
        core_root / "endpoints",
    }
    offenders = []
    for path in sorted(core_root.rglob("*.py")):
        if any(path.is_relative_to(prefix) for prefix in allowed_prefixes):
            continue
        text = path.read_text(encoding="utf-8")
        for needle in ("audio_chat.endpoints", "examples.", "endpoints/ios-phone", "endpoints/esp32-s3"):
            if needle in text:
                offenders.append(f"{path.relative_to(audio_root)}:{needle}")
    errors = [f"server SDK core imports endpoint/example boundary: {item}" for item in offenders]
    return {
        "ok": not errors,
        "offenders": offenders,
        "errors": errors,
    }


def _skipped(name: str, reason: str) -> dict:
    return {"ok": True, "name": name, "skipped": True, "reason": reason, "errors": []}
