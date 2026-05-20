from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]


pytestmark = pytest.mark.device_sdk


def test_typescript_device_sdk_native_contracts() -> None:
    """测试目标：确认 TypeScript Device SDK 的原生测试能消费协议 fixtures。

    测试方法：在 `audio-device/typescript` 下执行 `npm test`。
    预期结果：事件信封、设备注册 payload 和 stream codec 的 Node 测试全部通过。
    """

    if not shutil.which("npm"):
        pytest.skip("npm is not installed")
    _run(["npm", "test"], cwd=ROOT / "audio-device/typescript")


def test_swift_device_sdk_native_contracts() -> None:
    """测试目标：确认 Swift Device SDK 的原生测试能验证端侧协议对象。

    测试方法：在 `audio-device/swift` 下执行 `swift test`。
    预期结果：设备注册 payload 和 stream codec 测试全部通过。
    """

    if not shutil.which("swift"):
        pytest.skip("swift is not installed")
    _run(["swift", "test"], cwd=ROOT / "audio-device/swift", timeout_seconds=180)


def test_c_device_sdk_native_contracts() -> None:
    """测试目标：确认 C Device SDK 的原生测试能验证 stream codec 和注册 payload。

    测试方法：用 CMake 构建 `audio-device/c`，再执行 `ctest --output-on-failure`。
    预期结果：C 层二进制 frame 编解码和设备注册 JSON 测试全部通过。
    """

    if not shutil.which("cmake") or not shutil.which("ctest"):
        pytest.skip("cmake/ctest is not installed")
    cwd = ROOT / "audio-device/c"
    _run(["cmake", "-S", ".", "-B", "build"], cwd=cwd)
    _run(["cmake", "--build", "build"], cwd=cwd)
    _run(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=cwd)


def test_kotlin_device_sdk_native_contracts() -> None:
    """测试目标：确认 Kotlin Device SDK 在本机 Gradle 可用时能跑原生 contract。

    测试方法：优先使用仓库内 `gradlew`，否则使用系统 `gradle test`。
    预期结果：Gradle 可用时 Kotlin 设备注册 payload 和 stream codec 测试通过；没有
    Gradle 时明确 skip。
    """

    cwd = ROOT / "audio-device/kotlin"
    gradlew = cwd / "gradlew"
    if gradlew.exists():
        command = [str(gradlew), "test"]
    elif shutil.which("gradle"):
        command = ["gradle", "test"]
    else:
        pytest.skip("gradle is not installed and gradlew is not present")
    _run(command, cwd=cwd, timeout_seconds=180)


def _run(command: list[str], *, cwd: Path, timeout_seconds: int = 120) -> None:
    """执行原生 SDK 测试命令，并在失败时把输出带回 pytest。"""

    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"{' '.join(command)} failed in {cwd}\n{result.stdout}")
