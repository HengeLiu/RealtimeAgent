"""服务端通用启动命令。"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from infra.config import ServerSettings
from openaiglasses.cli.common import ensure_pythonpath, merged_env, require_command, shell_join


def _build_server_defaults() -> dict[str, str]:
    """从 `ServerSettings` 派生 CLI 子进程默认环境变量。

    主要逻辑：
    1. `ServerSettings` 是运行时配置的唯一默认值来源。
    2. CLI 只负责把默认值转换成 env 字符串，并保留 `HOST` / `PORT` 这两个本地配置别名。

    返回值：
    1. 可传给 `merged_env(...)` 的默认环境变量字典。
    """

    defaults = ServerSettings()
    return {
        "APP_ENV": defaults.environment,
        "HOST": defaults.host,
        "PORT": str(defaults.port),
        "LOG_LEVEL": defaults.log_level,
        "DASHSCOPE_API_KEY": defaults.dashscope_api_key,
        "DEVICE_TOKEN_MAP": defaults.device_token_map,
        "HEARTBEAT_INTERVAL_MS": str(defaults.heartbeat_interval_ms),
        "HEARTBEAT_TIMEOUT_MS": str(defaults.heartbeat_timeout_ms),
        "SERVER_DEVICE_ID": defaults.server_device_id,
        "VOICE_SESSION_MODE": defaults.voice_session_mode,
        "VOICE_MODEL_BASE_URL": defaults.voice_model_base_url,
        "VOICE_ASR_MODEL_NAME": defaults.voice_asr_model_name,
        "VOICE_ASR_MODE": defaults.voice_asr_mode,
        "VOICE_ASR_REALTIME_MODEL_NAME": defaults.voice_asr_realtime_model_name,
        "VOICE_ASR_REALTIME_TIMEOUT_MS": str(defaults.voice_asr_realtime_timeout_ms),
        "VOICE_ASR_REALTIME_MAX_SENTENCE_SILENCE_MS": str(
            defaults.voice_asr_realtime_max_sentence_silence_ms
        ),
        "AGENT_MODEL_NAME": defaults.agent_model_name,
        "VOICE_MODEL_NAME": defaults.voice_model_name,
        "VOICE_MODEL_VOICE": defaults.voice_model_voice,
        "VOICE_REPLY_MODE": defaults.voice_reply_mode,
        "VOICE_OMNI_REALTIME_MODEL_NAME": defaults.voice_omni_realtime_model_name,
        "VOICE_OMNI_REALTIME_URL": defaults.voice_omni_realtime_url,
        "VOICE_OMNI_PHOTO_WAIT_MS": str(defaults.voice_omni_photo_wait_ms),
        "VOICE_CONVERSATION_MODE": defaults.voice_conversation_mode,
        "VOICE_REALTIME_TURN_DETECTION": defaults.voice_realtime_turn_detection_type,
        "VOICE_REALTIME_SEMANTIC_VAD_THRESHOLD": str(defaults.voice_realtime_semantic_vad_threshold),
        "VOICE_REALTIME_SILENCE_DURATION_MS": str(defaults.voice_realtime_silence_duration_ms),
        "VOICE_REALTIME_PREFIX_PADDING_MS": str(defaults.voice_realtime_prefix_padding_ms),
        "VOICE_INPUT_MODE": defaults.voice_input_mode,
        "TTS_MODEL_NAME": defaults.tts_model_name,
        "TTS_VOICE": defaults.tts_voice,
        "TTS_WEBSOCKET_API_URL": defaults.tts_websocket_api_url,
        "TTS_SAMPLE_RATE_HZ": str(defaults.tts_sample_rate_hz),
        "VOICE_MODEL_TIMEOUT_MS": str(defaults.voice_model_timeout_ms),
        "VOICE_SYSTEM_PROMPT": defaults.voice_system_prompt,
        "ENABLE_PROGRESS_MESSAGE": "true" if defaults.enable_progress_message else "false",
        "MAX_SEGMENT_AUDIO_BYTES": str(defaults.max_segment_audio_bytes),
    }


SERVER_DEFAULTS = _build_server_defaults()

REMOTE_ENV_EXPORT_KEYS = set(SERVER_DEFAULTS) | {
    "SERVER_HOST",
    "SERVER_PORT",
    "SERVER_PUBLIC_HOST",
    "VOICE_RUNS_ROOT",
}


def build_parser() -> argparse.ArgumentParser:
    """构建服务端命令参数解析器。

    返回值：
    1. `argparse.ArgumentParser` 实例。
    """

    parser = argparse.ArgumentParser(prog="openaiglass.server.start", description="启动和管理 OpenAI Glasses 服务端")
    parser.add_argument("target", nargs="?", default="local", choices=["local", "remote"], help="运行目标")
    parser.add_argument("action", nargs="?", default="all", help="动作: start/logs/stop/all 或 sync/start/logs/stop/all")
    parser.add_argument("--app-module", default=os.environ.get("OPENAIGLASS_SERVER_APP", "app.main"), help="服务端入口模块")
    parser.add_argument("--repo-root", default=os.environ.get("REPO_ROOT", "."), help="项目根目录")
    parser.add_argument("--sdk-python-root", default="", help="SDK Python 源码目录")
    parser.add_argument("--app-root", default="", help="业务工程源码目录")
    parser.add_argument("--config", default=os.environ.get("LOCAL_SERVER_CONFIG_FILE", ""), help="本地 env 配置文件")
    parser.add_argument("--host", default=None, help="监听地址")
    parser.add_argument("--port", type=int, default=None, help="监听端口")
    parser.add_argument("--log-dir", default=os.environ.get("LOG_DIR", ""), help="日志目录")
    parser.add_argument("--log-file", default=os.environ.get("LOG_FILE", ""), help="日志文件")
    parser.add_argument("--pid-file", default=os.environ.get("PID_FILE", ""), help="PID 文件")
    parser.add_argument("--tail-lines", type=int, default=int(os.environ.get("TAIL_LINES", "120")), help="日志初始行数")
    parser.add_argument("--sync-item", action="append", default=[], help="远程同步条目，可重复传入")
    parser.add_argument("--remote-host", default=os.environ.get("REMOTE_HOST", "ali5"), help="远程 SSH host")
    parser.add_argument("--remote-dir", default=os.environ.get("REMOTE_DIR", ""), help="远程项目目录")
    parser.add_argument("--remote-log-dir", default=os.environ.get("REMOTE_LOG_DIR", ""), help="远程日志目录")
    parser.add_argument("--remote-log-file", default=os.environ.get("REMOTE_LOG_FILE", ""), help="远程日志文件")
    parser.add_argument("--remote-pid-file", default=os.environ.get("REMOTE_PID_FILE", ""), help="远程 PID 文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    """服务端命令主入口。

    参数：
    1. `argv`：命令行参数，不包含程序名。

    返回值：
    1. 进程退出码。

    异常情况：
    1. 参数非法或系统命令缺失时返回非零退出码。
    """

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.target == "local":
        return run_local(args)
    return run_remote(args)


def run_local(args: argparse.Namespace) -> int:
    """执行本地服务端动作。

    参数：
    1. `args`：解析后的命令参数。

    返回值：
    1. 进程退出码。
    """

    action = normalize_action(args.action)
    if action == "start":
        return start_local(args)
    if action == "logs":
        return tail_local_logs(args)
    if action == "stop":
        return stop_local(args)
    if action == "all":
        return run_local_foreground(args)
    print(f"Unknown local action: {args.action}", file=sys.stderr)
    return 2


def run_remote(args: argparse.Namespace) -> int:
    """执行远程服务端动作。

    参数：
    1. `args`：解析后的命令参数。

    返回值：
    1. 进程退出码。
    """

    action = normalize_action(args.action)
    if action == "sync":
        return sync_remote(args)
    if action == "start":
        return start_remote(args)
    if action == "stop":
        return stop_remote(args)
    if action == "logs":
        return tail_remote_logs(args)
    if action == "all":
        code = sync_remote(args)
        if code != 0:
            return code
        code = start_remote(args)
        if code != 0:
            return code
        return tail_remote_logs(args)
    print(f"Unknown remote action: {args.action}", file=sys.stderr)
    return 2


def normalize_action(action: str) -> str:
    """规范化动作名称。

    参数：
    1. `action`：原始动作。

    返回值：
    1. 规范化动作。
    """

    return action.strip().lower()


def _repo_root(args: argparse.Namespace) -> Path:
    """解析项目根目录。"""

    return Path(args.repo_root).resolve()


def _sdk_python_root(args: argparse.Namespace) -> Path:
    """解析 SDK Python 源码目录。"""

    if args.sdk_python_root:
        return Path(args.sdk_python_root).resolve()
    return _repo_root(args) / "openaiglass-sdk/server-python"


def _app_root(args: argparse.Namespace) -> Path | None:
    """解析业务工程源码目录。"""

    if not args.app_root:
        return None
    return Path(args.app_root).resolve()


def _config_path(args: argparse.Namespace) -> Path | None:
    """解析服务端配置文件路径。"""

    if not args.config:
        return None
    return Path(args.config).resolve()


def _log_dir(args: argparse.Namespace) -> Path:
    """解析日志目录。"""

    if args.log_dir:
        return Path(args.log_dir).resolve()
    return _repo_root(args) / "logs"


def _log_file(args: argparse.Namespace) -> Path:
    """解析日志文件路径。"""

    if args.log_file:
        return Path(args.log_file).resolve()
    return _log_dir(args) / "server.log"


def _pid_file(args: argparse.Namespace) -> Path:
    """解析 PID 文件路径。"""

    if args.pid_file:
        return Path(args.pid_file).resolve()
    return _log_dir(args) / "server.pid"


def _server_env(args: argparse.Namespace) -> dict[str, str]:
    """构造服务端运行环境变量。"""

    env = merged_env(_config_path(args), SERVER_DEFAULTS)
    if args.host:
        env["HOST"] = args.host
        env["SERVER_HOST"] = args.host
    else:
        env["SERVER_HOST"] = env.get("HOST", SERVER_DEFAULTS["HOST"])
    if args.port:
        env["PORT"] = str(args.port)
        env["SERVER_PORT"] = str(args.port)
    else:
        env["SERVER_PORT"] = env.get("PORT", SERVER_DEFAULTS["PORT"])
    env.setdefault("VOICE_RUNS_ROOT", str(_repo_root(args) / "runs/session"))
    # 后台启动时 stdout/stderr 已经重定向到 _log_file(args)。这里显式关闭
    # Python logger 的 FileHandler，避免同一条日志同时经 stdout 和 FileHandler 写入同一个文件。
    env["LOG_FILE"] = ""
    ensure_pythonpath(
        env,
        [path for path in [_sdk_python_root(args), _app_root(args), _repo_root(args)] if path is not None],
    )
    return env


def _read_pid(path: Path) -> int | None:
    """读取 PID 文件。"""

    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _pid_running(pid: int) -> bool:
    """判断进程是否仍在运行。"""

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _health_ok(port: str) -> bool:
    """检查服务端健康状态。"""

    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return 200 <= int(response.status) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def start_local(args: argparse.Namespace) -> int:
    """后台启动本地服务端。"""

    env = _server_env(args)
    log_file = _log_file(args)
    pid_file = _pid_file(args)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid = _read_pid(pid_file)
    if pid is not None and _pid_running(pid):
        print(f"[start] 服务端已在运行: pid={pid}")
        return 0
    pid_file.unlink(missing_ok=True)

    host = env.get("SERVER_HOST", env.get("HOST", "0.0.0.0"))
    port = env.get("SERVER_PORT", env.get("PORT", "8765"))
    command = [
        sys.executable,
        "-m",
        "openaiglasses.cli.server_runtime",
        "--app-module",
        args.app_module,
        "--host",
        host,
        "--port",
        str(port),
    ]
    print("[start] 启动本地服务端")
    print(f"[start] app_module={args.app_module}")
    print(f"[start] host={host} port={port}")
    print(f"[start] log_level={env.get('LOG_LEVEL', '')}")
    print(f"[start] log_file={log_file}")
    with log_file.open("ab") as output:
        process = subprocess.Popen(
            command,
            cwd=str(_repo_root(args)),
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_file.write_text(str(process.pid), encoding="utf-8")
    time.sleep(1)
    if process.poll() is not None or not _health_ok(str(port)):
        print("[start] 服务端启动失败，最近日志如下：", file=sys.stderr)
        print_tail(log_file, 120, stream=sys.stderr)
        pid_file.unlink(missing_ok=True)
        return 1
    print(f"[start] 启动成功: pid={process.pid}")
    print(f"[start] 运行态接口: http://127.0.0.1:{port}/api/runtime/devices")
    public_host = env.get("SERVER_PUBLIC_HOST", "").strip()
    if public_host:
        print(f"[start] 局域网控制地址: ws://{public_host}:{port}/ws/control")
        print(f"[start] 局域网运行态接口: http://{public_host}:{port}/api/runtime/devices")
    return 0


def run_local_foreground(args: argparse.Namespace) -> int:
    """前台运行本地服务端，当前命令退出时同步结束服务端。"""

    env = _server_env(args)
    pid_file = _pid_file(args)
    pid = _read_pid(pid_file)
    if pid is not None and _pid_running(pid):
        print(f"[run] 检测到已有后台服务端在运行: pid={pid}", file=sys.stderr)
        print("[run] 请先执行 openaiglass.server.stop，或改用 openaiglass.server.logs 查看日志", file=sys.stderr)
        return 1

    host = env.get("SERVER_HOST", env.get("HOST", "0.0.0.0"))
    port = env.get("SERVER_PORT", env.get("PORT", "8765"))
    command = [
        sys.executable,
        "-m",
        "openaiglasses.cli.server_runtime",
        "--app-module",
        args.app_module,
        "--host",
        host,
        "--port",
        str(port),
    ]
    print("[run] 前台启动本地服务端")
    print(f"[run] app_module={args.app_module}")
    print(f"[run] host={host} port={port}")
    print(f"[run] log_level={env.get('LOG_LEVEL', '')}")
    public_host = env.get("SERVER_PUBLIC_HOST", "").strip()
    if public_host:
        print(f"[run] 局域网控制地址: ws://{public_host}:{port}/ws/control")
        print(f"[run] 局域网运行态接口: http://{public_host}:{port}/api/runtime/devices")
    print("[run] 按 Ctrl+C 可停止服务端")

    process = subprocess.Popen(
        command,
        cwd=str(_repo_root(args)),
        env=env,
    )
    try:
        return process.wait()
    except KeyboardInterrupt:
        print("\n[run] 收到中断，正在停止服务端...")
        _terminate_process(process)
        return 130
    finally:
        if process.poll() is None:
            _terminate_process(process)


def _terminate_process(process: subprocess.Popen) -> None:
    """终止前台服务端子进程。"""

    process.terminate()
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def stop_local(args: argparse.Namespace) -> int:
    """停止本地服务端。"""

    pid_file = _pid_file(args)
    pid = _read_pid(pid_file)
    if pid is None or not _pid_running(pid):
        print("[stop] PID 文件中的服务端未运行")
        pid_file.unlink(missing_ok=True)
        return 0
    print(f"[stop] 停止服务端: pid={pid}")
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not _pid_running(pid):
            print(f"[stop] 已停止服务端: pid={pid}")
            pid_file.unlink(missing_ok=True)
            return 0
        time.sleep(0.2)
    print(f"[stop] 服务端仍未退出，执行强制停止: pid={pid}")
    os.kill(pid, signal.SIGKILL)
    pid_file.unlink(missing_ok=True)
    return 0


def tail_local_logs(args: argparse.Namespace) -> int:
    """跟随本地服务端日志。"""

    log_file = _log_file(args)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.touch(exist_ok=True)
    print(f"[logs] 跟随本地日志: {log_file}")
    return subprocess.run(["tail", "-n", str(args.tail_lines), "-F", str(log_file)], check=False).returncode


def print_tail(path: Path, lines: int, *, stream) -> None:
    """打印文件末尾若干行。"""

    if not path.exists():
        return
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in content[-lines:]:
        print(line, file=stream)


def _remote_dir(args: argparse.Namespace) -> str:
    """解析远程目录。"""

    if args.remote_dir:
        return args.remote_dir
    return f"/home/liuh/dev/{_repo_root(args).name}"


def _remote_log_dir(args: argparse.Namespace) -> str:
    """解析远程日志目录。"""

    return args.remote_log_dir or f"{_remote_dir(args)}/logs"


def _remote_log_file(args: argparse.Namespace) -> str:
    """解析远程日志文件。"""

    return args.remote_log_file or f"{_remote_log_dir(args)}/server.log"


def _remote_pid_file(args: argparse.Namespace) -> str:
    """解析远程 PID 文件。"""

    return args.remote_pid_file or f"{_remote_log_dir(args)}/server.pid"


def sync_remote(args: argparse.Namespace) -> int:
    """同步代码到远程服务器。"""

    require_command("ssh")
    require_command("rsync")
    repo_root = _repo_root(args)
    sync_items = args.sync_item or ["openaiglass-sdk", "openaiglass-for-blind", "pyproject.toml"]
    remote_dir = _remote_dir(args)
    print(f"[sync] Pushing code to {args.remote_host}:{remote_dir}")
    subprocess.run(["ssh", args.remote_host, f"mkdir -p {shell_join([remote_dir])}"], check=True)
    command = [
        "rsync",
        "-az",
        "--delete",
        "--exclude",
        ".git",
        "--exclude",
        ".venv",
        "--exclude",
        ".pytest_cache",
        "--exclude",
        ".mypy_cache",
        "--exclude",
        "__pycache__",
        "--exclude",
        "*.pyc",
        "--exclude",
        "build",
        "--exclude",
        "logs",
    ]
    command.extend(str(repo_root / item) for item in sync_items)
    command.append(f"{args.remote_host}:{remote_dir}/")
    return subprocess.run(command, check=False).returncode


def start_remote(args: argparse.Namespace) -> int:
    """启动远程服务端。"""

    require_command("ssh")
    env = _server_env(args)
    remote_dir = _remote_dir(args)
    remote_log_dir = _remote_log_dir(args)
    remote_log_file = _remote_log_file(args)
    remote_pid_file = _remote_pid_file(args)
    env_exports = " ".join(
        f"{key}={shell_join([value])}"
        for key, value in env.items()
        if key in REMOTE_ENV_EXPORT_KEYS
    )
    remote_command = (
        f"cd {shell_join([remote_dir])} && "
        "uv sync --python 3.11 && "
        f"{env_exports} "
        f"LOG_DIR={shell_join([remote_log_dir])} LOG_FILE={shell_join([remote_log_file])} PID_FILE={shell_join([remote_pid_file])} "
        "PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind:. "
        "uv run --python 3.11 python -m openaiglasses.cli server local start "
        f"--app-module {shell_join([args.app_module])} --repo-root . "
        "--sdk-python-root openaiglass-sdk/server-python --app-root openaiglass-for-blind"
    )
    print(f"[start] Starting server on {args.remote_host}")
    return subprocess.run(["ssh", args.remote_host, remote_command], check=False).returncode


def stop_remote(args: argparse.Namespace) -> int:
    """停止远程服务端。"""

    require_command("ssh")
    remote_dir = _remote_dir(args)
    remote_command = (
        f"cd {shell_join([remote_dir])} && "
        f"LOG_DIR={shell_join([_remote_log_dir(args)])} "
        f"LOG_FILE={shell_join([_remote_log_file(args)])} "
        f"PID_FILE={shell_join([_remote_pid_file(args)])} "
        "PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind:. "
        "uv run --python 3.11 python -m openaiglasses.cli server local stop "
        f"--app-module {shell_join([args.app_module])} --repo-root . "
        "--sdk-python-root openaiglass-sdk/server-python --app-root openaiglass-for-blind"
    )
    print(f"[stop] Stopping server on {args.remote_host}")
    return subprocess.run(["ssh", args.remote_host, remote_command], check=False).returncode


def tail_remote_logs(args: argparse.Namespace) -> int:
    """跟随远程服务端日志。"""

    require_command("ssh")
    remote_log_dir = _remote_log_dir(args)
    remote_log_file = _remote_log_file(args)
    command = f"mkdir -p {shell_join([remote_log_dir])} && touch {shell_join([remote_log_file])} && tail -n {args.tail_lines} -F {shell_join([remote_log_file])}"
    print(f"[logs] Tailing {remote_log_file} on {args.remote_host}")
    return subprocess.run(["ssh", "-t", args.remote_host, command], check=False).returncode
