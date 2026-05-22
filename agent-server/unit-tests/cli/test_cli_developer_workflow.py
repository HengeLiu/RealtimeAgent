from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path


AUDIO_ROOT = Path(__file__).resolve().parents[3]


DEVELOPER_COMMANDS = [
    "realtime-agent.config.sync",
    "realtime-agent.server.run",
    "realtime-agent.server.start",
    "realtime-agent.server.stop",
    "realtime-agent.server.logs",
    "realtime-agent.web.open",
    "realtime-agent.ios.open",
    "realtime-agent.ios.build-sim",
    "realtime-agent.esp32.config",
    "realtime-agent.esp32.build",
    "realtime-agent.esp32.flash",
    "realtime-agent.esp32.monitor",
    "realtime-agent.playback.glass",
    "realtime-agent.dev.preflight",
    "realtime-agent.dev.live-check",
    "realtime-agent.sdk.package-check",
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


def test_python_phone_endpoint_module_shows_help() -> None:
    """测试目标：确认 Python 手机参考端通过自身 module 入口启动，而不是 SDK CLI。

    测试方法：执行 `uv run python -m realtime_agent_python_phone_mock --help`。
    预期结果：命令输出帮助文本，说明端侧入口不依赖 `realtime_agent` SDK 命令命名空间。
    """

    completed = subprocess.run(
        ["uv", "run", "python", "-m", "realtime_agent_python_phone_mock", "--help"],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, f"stdout={completed.stdout}\nstderr={completed.stderr}"
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
            "realtime-agent.server.start",
            "--config",
            "examples/for-blind-app/agent-server/server.yaml",
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
        ["uv", "run", "realtime-agent.server.logs", "--log-file", str(log_file)],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert logs.returncode == 0
    assert "dry-run start" in logs.stdout

    stop = subprocess.run(
        ["uv", "run", "realtime-agent.server.stop", "--pid-file", str(pid_file), "--dry-run"],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert stop.returncode == 0


def test_web_open_print_url_is_side_effect_free() -> None:
    """测试目标：确认 browser-glass 打开命令支持无副作用检查模式。

    测试方法：执行 `realtime-agent.web.open --print-url`。
    预期结果：命令不启动浏览器，只输出可打开的 file URL。
    """

    completed = subprocess.run(
        ["uv", "run", "realtime-agent.web.open", "--print-url"],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().startswith("file://")


def test_web_open_serve_print_url_uses_local_http_origin() -> None:
    """测试目标：确认 browser-glass 可通过本地 HTTP origin 打开。

    测试方法：执行 `realtime-agent.web.open --serve --print-url`，不真正打开浏览器。
    预期结果：命令输出 `http://127.0.0.1:8766/.../browser-glass/index.html`
    且 query 中带有真正的 audio server URL，避免页面误连静态服务端口。
    """

    completed = subprocess.run(
        ["uv", "run", "realtime-agent.web.open", "--serve", "--print-url"],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    url = completed.stdout.strip()
    assert url.startswith("http://127.0.0.1:8766/")
    assert "/examples/dev-support/devices/browser-glass/index.html?" in url
    assert "server_url=http%3A%2F%2F127.0.0.1%3A8765" in url


def test_package_check_can_write_report(tmp_path) -> None:
    """测试目标：确认 SDK 包检查入口能生成 JSON 报告。

    测试方法：执行 `realtime-agent.sdk.package-check --report`。
    预期结果：报告 ok，且至少覆盖当前 pyproject 中的开发者命令。
    """

    report = tmp_path / "package-check.json"
    completed = subprocess.run(
        ["uv", "run", "realtime-agent.sdk.package-check", "--report", str(report)],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert report.exists()
    assert '"ok": true' in report.read_text(encoding="utf-8")
