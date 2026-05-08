from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def start(argv: list[str] | None = None) -> None:
    """后台启动 server。

    主要逻辑：
    1. 支持 `--dry-run` 写入 pid/log 文件，供开发者验收入口做无副作用检查。
    2. 非 dry-run 时使用当前 Python 解释器启动 `audio_chat.server.main`。

    参数：`argv` 为命令行参数。
    返回值：无。
    异常情况：进程创建失败或 pid 文件已存在时抛出异常。
    """

    parser = argparse.ArgumentParser(prog="audio-chat.server.start", description="后台启动 audio-chat server")
    parser.add_argument("--config", default="")
    parser.add_argument("--app-name", default="", help="应用名称，对应 app-examples/<app-name>")
    parser.add_argument("--app-root", default="app-examples", help="应用根目录，默认 app-examples")
    parser.add_argument("--app-module", default="")
    parser.add_argument("--pid-file", default="runs/audio-chat/server.pid")
    parser.add_argument("--log-file", default="runs/audio-chat/server.log")
    parser.add_argument("--dry-run", action="store_true", help="只写入开发验收文件，不启动真实 server")
    args = parser.parse_args(argv)

    pid_file = Path(args.pid_file)
    log_file = Path(args.log_file)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        _write_pid_file(
            pid_file,
            {
                "status": "dry_run",
                "pid": None,
                "config": args.config,
                "app_name": args.app_name,
                "app_root": args.app_root,
                "log_file": str(log_file),
            },
        )
        log_file.write_text("audio-chat server dry-run start\n", encoding="utf-8")
        print(f"server dry-run metadata written: {pid_file}")
        return

    if pid_file.exists():
        raise RuntimeError(f"pid file already exists: {pid_file}")
    command = [
        sys.executable,
        "-c",
        "from audio_chat.server import main; main()",
    ]
    if args.app_name:
        command.extend(["--app-name", args.app_name, "--app-root", args.app_root])
    else:
        command.extend(["--config", args.config or "app-examples/minimal/server.yaml"])
    if args.app_module:
        command.extend(["--app-module", args.app_module])
    log_handle = log_file.open("ab")
    process = subprocess.Popen(command, stdout=log_handle, stderr=subprocess.STDOUT)
    log_handle.close()
    _write_pid_file(
        pid_file,
        {
            "status": "running",
            "pid": process.pid,
            "config": args.config,
            "app_name": args.app_name,
            "app_root": args.app_root,
            "log_file": str(log_file),
        },
    )
    print(f"server started pid={process.pid} log={log_file}")


def stop(argv: list[str] | None = None) -> None:
    """停止由 `audio-chat.server.start` 启动的 server。"""

    parser = argparse.ArgumentParser(prog="audio-chat.server.stop", description="停止 audio-chat server")
    parser.add_argument("--pid-file", default="runs/audio-chat/server.pid")
    parser.add_argument("--dry-run", action="store_true", help="只验证 pid 文件路径")
    args = parser.parse_args(argv)
    pid_file = Path(args.pid_file)
    if args.dry_run:
        print(f"server stop dry-run pid_file={pid_file}")
        return
    if not pid_file.exists():
        print(f"server is not running: {pid_file}")
        return
    data = json.loads(pid_file.read_text(encoding="utf-8"))
    pid = data.get("pid")
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
            _wait_for_exit(int(pid), timeout_seconds=5.0)
        except ProcessLookupError:
            pass
    pid_file.unlink(missing_ok=True)
    print("server stopped")


def logs(argv: list[str] | None = None) -> None:
    """打印 server 日志尾部。"""

    parser = argparse.ArgumentParser(prog="audio-chat.server.logs", description="查看 audio-chat server 日志")
    parser.add_argument("--log-file", default="runs/audio-chat/server.log")
    parser.add_argument("--tail", type=int, default=80)
    args = parser.parse_args(argv)
    log_file = Path(args.log_file)
    if not log_file.exists():
        print(f"log file not found: {log_file}")
        return
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    print("\n".join(lines[-args.tail :]))


def _write_pid_file(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _wait_for_exit(pid: int, *, timeout_seconds: float) -> None:
    """等待后台 server 退出。

    主要逻辑：`SIGTERM` 后短暂轮询进程是否还存在；超时则升级为 `SIGKILL`，
    避免开发者反复 start/stop 后残留后台进程。
    参数：`pid` 为进程号，`timeout_seconds` 为等待秒数。
    返回值：无。
    异常情况：进程不存在时直接返回。
    """

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
