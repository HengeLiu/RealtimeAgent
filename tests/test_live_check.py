from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path

import yaml


AUDIO_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_server_config(path: Path, *, port: int) -> None:
    data = yaml.safe_load((AUDIO_ROOT / "app-examples/for-blind-app/server.yaml").read_text(encoding="utf-8"))
    data["server"]["host"] = "127.0.0.1"
    data["server"]["port"] = port
    data["server"]["public_url"] = f"http://127.0.0.1:{port}"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_live_check_reports_actionable_server_down_state(tmp_path: Path) -> None:
    """测试目标：确认 live-check 在 server 未启动时仍输出可操作诊断。

    测试方法：使用空闲端口配置执行 `audio-chat.dev.live-check`。
    预期结果：命令成功生成报告，`live_server` 检查为 false，并给出启动 server 的建议。
    """

    port = _free_port()
    config = tmp_path / "server.yaml"
    _write_server_config(config, port=port)
    generated = tmp_path / "generated"
    subprocess.run(
        [
            "uv",
            "run",
            "audio-chat.config.sync",
            "--server-config",
            str(config),
            "--server-url",
            f"http://127.0.0.1:{port}",
            "--output-dir",
            str(generated),
        ],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    report = tmp_path / "live-check.json"

    completed = subprocess.run(
        [
            "uv",
            "run",
            "audio-chat.dev.live-check",
            "--config",
            str(config),
            "--generated-dir",
            str(generated),
            "--report",
            str(report),
        ],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    checks = {check["name"]: check for check in data["checks"]}
    assert checks["live_server"]["ok"] is False
    assert checks["endpoint_config_consistency"]["ok"] is True
    assert data["next_actions"]


def test_live_check_reports_server_health_when_running(tmp_path: Path) -> None:
    """测试目标：确认 live-check 能识别已启动 server 并读取 debug devices。

    测试方法：启动临时后台 server，轮询后执行 live-check。
    预期结果：`live_server` 检查通过，health 返回 `audio-chat.v1`。
    """

    port = _free_port()
    config = tmp_path / "server.yaml"
    _write_server_config(config, port=port)
    generated = tmp_path / "generated"
    subprocess.run(
        [
            "uv",
            "run",
            "audio-chat.config.sync",
            "--server-config",
            str(config),
            "--server-url",
            f"http://127.0.0.1:{port}",
            "--output-dir",
            str(generated),
        ],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    pid_file = tmp_path / "server.pid"
    log_file = tmp_path / "server.log"
    start = subprocess.run(
        [
            "uv",
            "run",
            "audio-chat.server.start",
            "--config",
            str(config),
            "--pid-file",
            str(pid_file),
            "--log-file",
            str(log_file),
        ],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert start.returncode == 0, start.stderr
    try:
        deadline = time.monotonic() + 10
        live_report = tmp_path / "live-running.json"
        completed = None
        while time.monotonic() < deadline:
            completed = subprocess.run(
                [
                    "uv",
                    "run",
                    "audio-chat.dev.live-check",
                    "--config",
                    str(config),
                    "--generated-dir",
                    str(generated),
                    "--report",
                    str(live_report),
                ],
                cwd=AUDIO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            if completed.returncode == 0:
                data = json.loads(live_report.read_text(encoding="utf-8"))
                if {check["name"]: check for check in data["checks"]}["live_server"]["ok"]:
                    break
            time.sleep(0.2)
        assert completed is not None
        assert completed.returncode == 0, completed.stderr
        data = json.loads(live_report.read_text(encoding="utf-8"))
        checks = {check["name"]: check for check in data["checks"]}
        assert checks["live_server"]["ok"] is True
        assert checks["live_server"]["server_health"]["protocol_version"] == "audio-chat.v1"
    finally:
        subprocess.run(
            ["uv", "run", "audio-chat.server.stop", "--pid-file", str(pid_file)],
            cwd=AUDIO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
