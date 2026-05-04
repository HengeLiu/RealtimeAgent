"""检查三端 SDK 是否可以被打包、安装和导入。"""

from __future__ import annotations

import json
import os
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT_DIR = Path.cwd().resolve()
SDK_ROOT = ROOT_DIR / "openaiglass-sdk"
SDK_DIR = SDK_ROOT / "server-python"
PHONE_IOS_DIR = SDK_ROOT / "phone-ios"
GLASS_ESP32_DIR = SDK_ROOT / "glass-esp32"


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="检查三端 SDK 是否可以被打包、安装和导入")
    parser.add_argument("--repo-root", default=".", help="项目根目录")
    return parser.parse_args()


def configure_paths(args: argparse.Namespace) -> None:
    """根据命令行参数设置仓库路径。"""

    global ROOT_DIR, SDK_ROOT, SDK_DIR, PHONE_IOS_DIR, GLASS_ESP32_DIR
    ROOT_DIR = Path(args.repo_root).resolve()
    SDK_ROOT = ROOT_DIR / "openaiglass-sdk"
    SDK_DIR = SDK_ROOT / "server-python"
    PHONE_IOS_DIR = SDK_ROOT / "phone-ios"
    GLASS_ESP32_DIR = SDK_ROOT / "glass-esp32"


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> dict[str, object]:
    """执行命令并返回结构化结果。

    参数：
    1. `command`：待执行命令。
    2. `cwd`：命令工作目录。
    3. `env`：可选环境变量。

    返回值：
    1. 包含退出码、标准输出和标准错误的字典。
    """

    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _python_with_build_backend() -> str:
    """选择具备 setuptools/wheel 的 Python 解释器。

    构建解释器必须与 SDK 的 `requires-python >=3.11` 一致。
    如果当前解释器缺少构建后端，应先在当前环境安装 `setuptools wheel`，
    不应回退到系统 Python 3.9。
    """

    if sys.version_info < (3, 11):
        raise RuntimeError(f"SDK 构建需要 Python >= 3.11，当前为 {sys.version.split()[0]}")

    probe = _run(
        [sys.executable, "-c", "import setuptools, wheel"],
        cwd=ROOT_DIR,
    )
    if probe["returncode"] == 0:
        return sys.executable

    raise RuntimeError("当前 Python 缺少 setuptools 或 wheel，请先执行：uv pip install setuptools wheel")


def _build_wheel(build_python: str, dist_dir: Path) -> Path:
    """构建 SDK wheel。"""

    build_code = f"""
import os
from pathlib import Path

import setuptools.build_meta

os.chdir({str(SDK_DIR)!r})
wheel_name = setuptools.build_meta.build_wheel({str(dist_dir)!r})
print(Path({str(dist_dir)!r}) / wheel_name)
"""
    result = _run(
        [
            build_python,
            "-c",
            build_code,
        ],
        cwd=ROOT_DIR,
    )
    if result["returncode"] != 0:
        raise RuntimeError(json.dumps(result, ensure_ascii=False, indent=2))

    wheels = sorted(dist_dir.glob("openaiglasses_sdk-*.whl"))
    if not wheels:
        raise RuntimeError(f"未生成 openaiglasses-sdk wheel: {dist_dir}")
    return wheels[-1]


def _ensure_pip_available() -> None:
    """确保当前解释器可执行 pip。

    uv 创建的虚拟环境可能默认不带 pip。为了验证真实的 `pip install`
    行为，脚本会优先通过标准库 `ensurepip` 补齐 pip。
    """

    probe = _run([sys.executable, "-m", "pip", "--version"], cwd=ROOT_DIR)
    if probe["returncode"] == 0:
        return
    ensure = _run([sys.executable, "-m", "ensurepip", "--upgrade"], cwd=ROOT_DIR)
    if ensure["returncode"] != 0:
        raise RuntimeError(json.dumps(ensure, ensure_ascii=False, indent=2))


def _install_and_import(wheel_path: Path, work_dir: Path) -> dict[str, object]:
    """安装 wheel 并验证导入。

    主要逻辑：
    1. 把刚构建出的 SDK wheel 安装到临时 target 目录。
    2. 在不位于仓库源码树的工作目录中导入公开 API 和内部运行时模块。
    3. 在脱离仓库源码路径的目录中导入公开 API 和内部运行时模块。
    """

    _ensure_pip_available()
    target_dir = work_dir / "site-packages"

    install = _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(target_dir),
            "--no-deps",
            str(wheel_path),
        ],
        cwd=work_dir,
    )
    if install["returncode"] != 0:
        raise RuntimeError(json.dumps(install, ensure_ascii=False, indent=2))

    code = """
import importlib
from importlib.metadata import version

from openaiglasses import OpenAIGlassesSDK, ServerSettings

sdk = OpenAIGlassesSDK()
settings = ServerSettings()
assert sdk.registry is not None
assert settings.port > 0

for module_name in [
    "openaiglasses",
    "agent_core.skills",
    "infra.clock",
    "api.http_server",
    "runtime.voice_runtime",
    "runtime.voice_gateway",
    "runtime.voice_constants",
    "runtime.voice_models",
    "runtime.voice_state",
    "runtime.audio_utils",
    "runtime.model_payloads",
    "runtime.omni.realtime_client",
    "runtime.omni.omni_voice_server",
    "runtime.text.speech_clients",
    "runtime.text.text_voice_server",
    "runtime.text.text_dialog_state_machine",
]:
    importlib.import_module(module_name)

print(version("openaiglasses-sdk"))
"""
    import_env = {**os.environ, "PYTHONPATH": str(target_dir)}
    imported = _run([sys.executable, "-c", code], cwd=work_dir, env=import_env)
    if imported["returncode"] != 0:
        raise RuntimeError(json.dumps(imported, ensure_ascii=False, indent=2))
    return {
        "install": install,
        "import": imported,
    }


def _load_manifest(manifest_path: Path, required_fields: list[str]) -> dict[str, Any]:
    """读取并校验 SDK 端侧包清单。

    参数：
    1. `manifest_path`：清单文件路径。
    2. `required_fields`：必须存在的顶层字段。

    返回值：
    1. 解析后的 JSON 对象。

    异常：
    1. 文件不存在、JSON 不是对象或缺少字段时抛出 `RuntimeError`。
    """

    if not manifest_path.exists():
        raise RuntimeError(f"缺少端侧 SDK 包清单: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"端侧 SDK 包清单 JSON 无法解析: {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError(f"端侧 SDK 包清单必须是 JSON 对象: {manifest_path}")

    missing = [field for field in required_fields if field not in manifest]
    if missing:
        raise RuntimeError(f"端侧 SDK 包清单缺少字段 {missing}: {manifest_path}")
    return manifest


def _require_relative_files(base_dir: Path, paths: list[str], *, field_name: str) -> list[str]:
    """确认清单中声明的相对文件都存在。

    参数：
    1. `base_dir`：相对路径基准目录。
    2. `paths`：清单中声明的相对路径列表。
    3. `field_name`：用于错误信息的字段名。

    返回值：
    1. 归一化后的路径字符串列表。
    """

    if not isinstance(paths, list) or not all(isinstance(item, str) and item for item in paths):
        raise RuntimeError(f"{field_name} 必须是非空字符串列表")

    normalized: list[str] = []
    missing: list[str] = []
    for item in paths:
        candidate = (base_dir / item).resolve()
        if not candidate.exists():
            missing.append(item)
        normalized.append(item)
    if missing:
        raise RuntimeError(f"{field_name} 中存在缺失文件: {missing}")
    return normalized


def _check_ios_package_shape() -> dict[str, Any]:
    """检查 iOS SDK 源码包形态。

    主要逻辑：
    1. 读取 `phone-ios/package-manifest.json`。
    2. 校验 Xcode 工程、运行时代码、测试代码和示例配置是否存在。
    3. 返回可写入 package-check 报告的结构化结果。
    """

    manifest = _load_manifest(
        PHONE_IOS_DIR / "package-manifest.json",
        [
            "name",
            "version",
            "package_type",
            "minimum_ios",
            "minimum_swift",
            "xcode_project",
            "runtime_files",
            "test_files",
            "resource_files",
            "public_capabilities",
        ],
    )
    xcode_project = PHONE_IOS_DIR / str(manifest["xcode_project"])
    if not xcode_project.exists():
        raise RuntimeError(f"iOS SDK Xcode 工程不存在: {xcode_project}")

    runtime_files = _require_relative_files(PHONE_IOS_DIR, manifest["runtime_files"], field_name="runtime_files")
    test_files = _require_relative_files(PHONE_IOS_DIR, manifest["test_files"], field_name="test_files")
    resource_files = _require_relative_files(PHONE_IOS_DIR, manifest["resource_files"], field_name="resource_files")
    capabilities = manifest["public_capabilities"]
    if not isinstance(capabilities, list) or not capabilities:
        raise RuntimeError("public_capabilities 必须是非空列表")

    return {
        "ok": True,
        "name": manifest["name"],
        "version": manifest["version"],
        "package_type": manifest["package_type"],
        "xcode_project": str(xcode_project.relative_to(ROOT_DIR)),
        "runtime_files": len(runtime_files),
        "test_files": len(test_files),
        "resource_files": len(resource_files),
        "public_capabilities": capabilities,
    }


def _check_esp32_package_shape() -> dict[str, Any]:
    """检查 ESP32 SDK 源码包形态。

    主要逻辑：
    1. 读取 `glass-esp32/component-manifest.json`。
    2. 校验 ESP-IDF 工程入口、组件文件、默认配置和分区表是否存在。
    3. 返回可写入 package-check 报告的结构化结果。
    """

    manifest = _load_manifest(
        GLASS_ESP32_DIR / "component-manifest.json",
        [
            "name",
            "version",
            "package_type",
            "idf_target",
            "minimum_esp_idf",
            "project_files",
            "component_files",
            "managed_dependencies",
            "public_capabilities",
        ],
    )
    project_files = _require_relative_files(GLASS_ESP32_DIR, manifest["project_files"], field_name="project_files")
    component_files = _require_relative_files(
        GLASS_ESP32_DIR,
        manifest["component_files"],
        field_name="component_files",
    )
    dependencies = manifest["managed_dependencies"]
    capabilities = manifest["public_capabilities"]
    if not isinstance(dependencies, dict) or not dependencies:
        raise RuntimeError("managed_dependencies 必须是非空对象")
    if not isinstance(capabilities, list) or not capabilities:
        raise RuntimeError("public_capabilities 必须是非空列表")

    return {
        "ok": True,
        "name": manifest["name"],
        "version": manifest["version"],
        "package_type": manifest["package_type"],
        "idf_target": manifest["idf_target"],
        "project_files": len(project_files),
        "component_files": len(component_files),
        "managed_dependencies": dependencies,
        "public_capabilities": capabilities,
    }


def main() -> int:
    """CLI 入口。"""

    args = parse_args()
    configure_paths(args)
    with tempfile.TemporaryDirectory(prefix="openaiglasses-sdk-package-") as tmp:
        work_dir = Path(tmp)
        dist_dir = work_dir / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        build_python = _python_with_build_backend()
        wheel_path = _build_wheel(build_python, dist_dir)
        import_result = _install_and_import(wheel_path, work_dir)
        report = {
            "ok": True,
            "build_python": build_python,
            "wheel": str(wheel_path),
            "import_stdout": import_result["import"]["stdout"],
            "ios_package": _check_ios_package_shape(),
            "esp32_package": _check_esp32_package_shape(),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
