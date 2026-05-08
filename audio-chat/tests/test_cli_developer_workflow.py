from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path


AUDIO_ROOT = Path(__file__).resolve().parents[1]


DEVELOPER_COMMANDS = [
    "audio-chat.config.sync",
    "audio-chat.server.run",
    "audio-chat.server.start",
    "audio-chat.server.stop",
    "audio-chat.server.logs",
    "audio-chat.phone.mock",
    "audio-chat.web.open",
    "audio-chat.ios.open",
    "audio-chat.ios.build-sim",
    "audio-chat.esp32.config",
    "audio-chat.esp32.build",
    "audio-chat.esp32.flash",
    "audio-chat.esp32.monitor",
    "audio-chat.playback.glass",
    "audio-chat.dev.preflight",
    "audio-chat.dev.live-check",
    "audio-chat.sdk.package-check",
]


def test_developer_entry_points_exist_in_pyproject() -> None:
    """测试目标：冻结开发者可用 CLI 命令集合。

    测试方法：读取 `pyproject.toml` 的 project.scripts。
    预期结果：P0-A 要求的 8 个命令全部存在，后续并行线路可以直接引用。
    """

    data = tomllib.loads((AUDIO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]

    for command in DEVELOPER_COMMANDS:
        assert command in scripts


def test_developer_entry_points_show_help() -> None:
    """测试目标：确认开发者入口命令能输出帮助信息。

    测试方法：逐个执行 `uv run <command> --help`。
    预期结果：命令以 0 退出，并输出 usage/help 文本，不进入真实网络链路。
    """

    for command in DEVELOPER_COMMANDS:
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


def test_server_start_stop_logs_dry_run_generate_files(tmp_path) -> None:
    """测试目标：确认 server 管理命令能在测试目录生成预期文件。

    测试方法：使用 `--dry-run` 启动和停止，并读取 pid/log 文件。
    预期结果：不启动真实 server，但能生成后续联调脚本依赖的元数据和日志文件。
    """

    pid_file = tmp_path / "server.pid"
    log_file = tmp_path / "server.log"
    start = subprocess.run(
        [
            "uv",
            "run",
            "audio-chat.server.start",
            "--config",
            "app-examples/basic-app/server.yaml",
            "--pid-file",
            str(pid_file),
            "--log-file",
            str(log_file),
            "--dry-run",
        ],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert start.returncode == 0, start.stderr
    assert pid_file.exists()
    assert log_file.read_text(encoding="utf-8").strip()

    logs = subprocess.run(
        ["uv", "run", "audio-chat.server.logs", "--log-file", str(log_file)],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert logs.returncode == 0
    assert "dry-run start" in logs.stdout

    stop = subprocess.run(
        ["uv", "run", "audio-chat.server.stop", "--pid-file", str(pid_file), "--dry-run"],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert stop.returncode == 0


def test_web_open_print_url_is_side_effect_free() -> None:
    """测试目标：确认 browser-glass 打开命令支持无副作用检查模式。

    测试方法：执行 `audio-chat.web.open --print-url`。
    预期结果：命令不启动浏览器，只输出可打开的 file URL。
    """

    completed = subprocess.run(
        ["uv", "run", "audio-chat.web.open", "--print-url"],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().startswith("file://")


def test_package_check_can_write_report(tmp_path) -> None:
    """测试目标：确认 SDK 包检查入口能生成 JSON 报告。

    测试方法：执行 `audio-chat.sdk.package-check --report`。
    预期结果：报告 ok，且至少覆盖当前 pyproject 中的开发者命令。
    """

    report = tmp_path / "package-check.json"
    completed = subprocess.run(
        ["uv", "run", "audio-chat.sdk.package-check", "--report", str(report)],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert report.exists()
    assert '"ok": true' in report.read_text(encoding="utf-8")
