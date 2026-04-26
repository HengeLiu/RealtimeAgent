"""检查 Python SDK 是否可以被打包、安装和导入。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SDK_DIR = ROOT_DIR / "sdk" / "python"


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
    "openaiglasses.testing",
    "agent_core.skills",
    "infra.clock",
    "api.http_server",
    "runtime.voice_runtime",
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


def main() -> int:
    """CLI 入口。"""

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
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
