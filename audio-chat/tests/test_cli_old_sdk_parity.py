from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path


AUDIO_ROOT = Path(__file__).resolve().parents[1]


OLD_SDK_PARITY_COMMANDS = [
    "audio-chat.config.sync",
    "audio-chat.server.run",
    "audio-chat.server.start",
    "audio-chat.server.stop",
    "audio-chat.server.logs",
    "audio-chat.phone.mock",
    "audio-chat.playback.glass",
    "audio-chat.web.open",
    "audio-chat.ios.open",
    "audio-chat.ios.build-sim",
    "audio-chat.esp32.config",
    "audio-chat.esp32.build",
    "audio-chat.esp32.flash",
    "audio-chat.esp32.monitor",
    "audio-chat.dev.preflight",
    "audio-chat.dev.live-check",
    "audio-chat.sdk.package-check",
]


def test_old_sdk_parity_cli_entry_points_exist_and_show_help() -> None:
    """测试目标：冻结老 SDK 可用性对齐阶段的 CLI 命令集合。

    测试方法：读取 `pyproject.toml` 并逐个执行 `uv run <command> --help`。
    预期结果：所有命令都有 entry point，且帮助输出不触发真实设备或网络链路。
    """

    scripts = tomllib.loads((AUDIO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    missing = [command for command in OLD_SDK_PARITY_COMMANDS if command not in scripts]
    assert not missing

    for command in OLD_SDK_PARITY_COMMANDS:
        completed = subprocess.run(
            ["uv", "run", command, "--help"],
            cwd=AUDIO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, f"{command}\nstdout={completed.stdout}\nstderr={completed.stderr}"
        output = f"{completed.stdout}\n{completed.stderr}".lower()
        assert "usage" in output or "help" in output


def test_ios_and_web_reference_commands_have_side_effect_free_modes() -> None:
    """测试目标：确认桌面端侧入口可在 CI 或无桌面环境中做无副作用检查。

    测试方法：执行 web/iOS 打开命令的 print/dry-run 模式。
    预期结果：命令返回真实可检查路径或构建命令，不打开浏览器、Xcode 或 Simulator。
    """

    commands = [
        ["uv", "run", "audio-chat.web.open", "--print-url"],
        ["uv", "run", "audio-chat.ios.open", "--print-path"],
        ["uv", "run", "audio-chat.ios.build-sim", "--dry-run"],
    ]
    for command in commands:
        completed = subprocess.run(command, cwd=AUDIO_ROOT, text=True, capture_output=True, check=False)
        assert completed.returncode == 0, f"{command}\nstdout={completed.stdout}\nstderr={completed.stderr}"
        assert completed.stdout.strip()


def test_esp32_real_actions_fail_without_firmware_project(tmp_path: Path) -> None:
    """测试目标：确认 ESP32 真机命令缺少固件工程时明确失败，不假装成功。

    测试方法：把 `--project-dir` 指向空目录并执行 dry-run build。
    预期结果：命令非零退出，错误中包含 ESP-IDF project 诊断。
    """

    project_dir = tmp_path / "missing-firmware"
    project_dir.mkdir()
    completed = subprocess.run(
        ["uv", "run", "audio-chat.esp32.build", "--project-dir", str(project_dir), "--dry-run"],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "ESP-IDF project not found" in completed.stderr
