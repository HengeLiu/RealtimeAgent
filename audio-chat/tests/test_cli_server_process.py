from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen


AUDIO_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_server_start_stop_does_not_leave_background_process(tmp_path) -> None:
    """测试目标：验证 `server.start/stop` 真实管理后台进程。

    测试方法：生成临时 server 配置，启动后台进程，轮询 `/api/health`，再执行
    `server.stop`。
    预期结果：服务可访问，stop 后 pid 文件删除且原进程不再存活。
    """

    port = _free_port()
    config = tmp_path / "server.yaml"
    config.write_text(
        f"""
server:
  host: "127.0.0.1"
  port: {port}
  public_url: "http://127.0.0.1:{port}"
auth:
  mode: "disabled"
agent:
  mode: "text"
  text:
    model_provider: "mock"
    asr_provider: "mock"
    tts_provider: "mock"
tools:
  enabled: true
  discover:
    enabled: false
tasks:
  enabled: false
dev_checks:
  require_recent_playback_ok: false
""",
        encoding="utf-8",
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
    pid = int(json.loads(pid_file.read_text(encoding="utf-8"))["pid"])
    try:
        deadline = time.monotonic() + 10
        health = None
        while time.monotonic() < deadline:
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                    health = json.loads(response.read().decode("utf-8"))
                    break
            except Exception:
                time.sleep(0.1)
        assert health == {"status": "ok", "protocol_version": "audio-chat.v1", "app_name": tmp_path.name}
    finally:
        stop = subprocess.run(
            ["uv", "run", "audio-chat.server.stop", "--pid-file", str(pid_file)],
            cwd=AUDIO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if _is_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        assert stop.returncode == 0, stop.stderr

    assert not pid_file.exists()
    assert not _is_alive(pid)
    assert log_file.exists()
