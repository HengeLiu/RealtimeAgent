from __future__ import annotations

import subprocess
from pathlib import Path

from audio_chat.cli.esp32 import _esp32_project_manifest_check


AUDIO_ROOT = Path(__file__).resolve().parents[1]


def test_esp32_reference_firmware_manifest_is_complete() -> None:
    """测试目标：确认 ESP32-S3 参考端具备 package-check 需要的工程骨架。

    测试方法：检查 `device-examples/native-esp32-glass/firmware` 下 ESP-IDF 必要文件和组件依赖。
    预期结果：CMake、sdkconfig defaults、component manifest 和参考 main 文件都存在，
    且 manifest 声明 WebSocket 与 JSON 依赖。
    """

    result = _esp32_project_manifest_check(AUDIO_ROOT / "device-examples/native-esp32-glass/firmware")

    assert result["ok"] is True
    assert result["errors"] == []


def test_esp32_build_dry_run_reports_command_with_reference_project() -> None:
    """测试目标：确认 ESP32 build 命令能在无副作用模式下使用参考工程。

    测试方法：执行 `audio-chat.esp32.build --dry-run`。
    预期结果：命令返回 0，并输出将要执行的 idf.py build 命令；不要求本机安装 ESP-IDF。
    """

    completed = subprocess.run(
        ["uv", "run", "audio-chat.esp32.build", "--dry-run"],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "device-examples/native-esp32-glass/firmware" in completed.stdout
    assert "build" in completed.stdout


def test_esp32_monitor_dry_run_reports_command_with_reference_project() -> None:
    """测试目标：确认 ESP32 monitor 命令能在无副作用模式下使用参考工程。

    测试方法：执行 `audio-chat.esp32.monitor --dry-run`。
    预期结果：命令返回 0，并输出将要执行的 monitor 命令。
    """

    completed = subprocess.run(
        ["uv", "run", "audio-chat.esp32.monitor", "--dry-run", "--port", "/dev/tty.fake"],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "monitor" in completed.stdout
    assert "/dev/tty.fake" in completed.stdout
