from __future__ import annotations

import contextlib
import json
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import yaml

from .case_schema import load_case
from .report import write_summary_report
from .runner import run_case_sync


@dataclass(frozen=True)
class ConversationRegressionTarget:
    """单条 conversation 回归目标。

    主要功能：描述需要派生的 server 配置、监听端口和回放报告位置。
    主要属性：`name` 是报告名称，`agent_mode` 是 `omni` 或 `vision`。
    """

    name: str
    agent_mode: str
    port: int
    turn_detection: str | None = None
    audio_vad: str | None = None


DEFAULT_TARGETS = [
    ConversationRegressionTarget(name="omni-manual", agent_mode="omni", port=9876, turn_detection="manual", audio_vad="server_only"),
    ConversationRegressionTarget(name="vl-conversation", agent_mode="vision", port=9877, audio_vad="provider"),
]


def run_conversation_regression(
    *,
    base_config: str | Path,
    case_path: str | Path,
    work_root: str | Path | None = None,
    report_path: str | Path,
    targets: list[ConversationRegressionTarget] | None = None,
) -> dict[str, Any]:
    """执行 Omni Manual 和 VL conversation 真实回放验收。

    主要逻辑：为每个目标从正式示例 `server.yaml` 派生临时 conversation 配置，
    启动真实 server，等待 `/api/health` 后用 `python-playback-glass` 通过 WebSocket
    执行回放 case，最后写出统一汇总报告。
    参数：`base_config` 是正式示例配置；`case_path` 是回放 case；`work_root`
    为临时 runs/config 根目录；`report_path` 为汇总报告。
    返回值：汇总报告字典。
    异常情况：server 启动失败、回放失败或健康检查超时时抛出异常。
    """

    base_config_path = Path(base_config).expanduser().resolve()
    case = load_case(case_path)
    target_items = targets or DEFAULT_TARGETS
    if work_root is None:
        temp_context = tempfile.TemporaryDirectory(prefix="realtime-agent-conversation-regression-")
        root = Path(temp_context.name)
    else:
        temp_context = None
        root = Path(work_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
    try:
        reports = []
        target_summaries = []
        for target in target_items:
            if not port_is_free(target.port):
                raise RuntimeError(f"conversation regression port is already in use: {target.port}")
            target_root = root / target.name
            target_root.mkdir(parents=True, exist_ok=True)
            config_path = write_conversation_server_config(base_config_path=base_config_path, target=target, target_root=target_root)
            server = _start_server(config_path)
            try:
                server_url = f"http://127.0.0.1:{target.port}"
                _wait_for_health(server_url, process=server)
                report = run_case_sync(case, server_url=server_url, runs_root=target_root / "runs", report_dir=target_root)
                reports.append(report)
                target_summaries.append(
                    {
                        "name": target.name,
                        "agent_mode": target.agent_mode,
                        "server_url": server_url,
                        "config": str(config_path),
                        "runs_root": str(target_root / "runs"),
                        "ok": report.ok,
                        "case_report": report.to_dict(),
                    }
                )
            finally:
                _stop_server(server)
        summary = write_summary_report(reports=reports, report_path=report_path, suite_id="conversation-regression")
        summary["targets"] = target_summaries
        Path(report_path).expanduser().resolve().write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return summary
    finally:
        if temp_context is not None:
            temp_context.cleanup()


def write_conversation_server_config(
    *,
    base_config_path: str | Path,
    target: ConversationRegressionTarget,
    target_root: str | Path,
) -> Path:
    """从正式示例配置派生 conversation 回归 server.yaml。

    主要逻辑：只修改运行时、端口、agent mode 和与目标有关的 VAD/turn detection；
    模型、prompt、provider、输出和工具配置都继承正式示例，保证验收贴近真实链路。
    参数：`base_config_path` 是源配置，`target` 是回归目标，`target_root` 是输出目录。
    返回值：生成的 `server.yaml` 路径。
    异常情况：源 YAML 非字典时抛出 ValueError。
    """

    path = Path(base_config_path).expanduser().resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"server config root must be mapping: {path}")
    root = Path(target_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    data.setdefault("paths", {})["runtime_root"] = str(root / "runs")
    server = data.setdefault("server", {})
    server["host"] = "127.0.0.1"
    server["port"] = target.port
    server["public_url"] = f"http://127.0.0.1:{target.port}"
    agent = data.setdefault("agent", {})
    agent["mode"] = target.agent_mode
    agent.setdefault("conversation", {})["runtime"] = "conversation"
    if target.turn_detection is not None:
        agent.setdefault("omni", {})["turn_detection"] = target.turn_detection
    if target.audio_vad is not None:
        data.setdefault("audio_pipeline", {})["vad"] = target.audio_vad
    if target.name == "omni-manual":
        audio_pipeline = data.setdefault("audio_pipeline", {})
        audio_pipeline["vad_rms_threshold"] = int(audio_pipeline.get("vad_rms_threshold") or 96)
        audio_pipeline["vad_silence_timeout_ms"] = int(audio_pipeline.get("vad_silence_timeout_ms") or 500)
    out = root / "server.yaml"
    out.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out


def _start_server(config_path: Path) -> subprocess.Popen:
    """启动测试 server 子进程。"""

    return subprocess.Popen(
        ["uv", "run", "realtime-agent.server.run", "--config", str(config_path)],
        cwd=Path.cwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_for_health(server_url: str, *, process: subprocess.Popen, timeout_seconds: float = 20) -> None:
    """等待 server 健康检查通过。"""

    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = _read_process_output(process)
            raise RuntimeError(f"server exited before health check: code={process.returncode}\n{output}")
        try:
            with urlopen(f"{server_url}/api/health", timeout=1) as response:  # noqa: S310 - 本地回归入口
                if response.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.2)
    raise TimeoutError(f"server health check timed out: {server_url} last_error={last_error}")


def _stop_server(process: subprocess.Popen) -> None:
    """停止测试 server 子进程。"""

    if process.poll() is not None:
        _read_process_output(process)
        return
    process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)
    if process.poll() is None:
        process.kill()
        process.wait(timeout=5)
    _read_process_output(process)


def _read_process_output(process: subprocess.Popen) -> str:
    """读取 server 子进程已输出内容。"""

    if process.stdout is None:
        return ""
    with contextlib.suppress(Exception):
        return process.stdout.read() or ""
    return ""


def port_is_free(port: int) -> bool:
    """判断本地端口是否可用于测试 server。

    参数：`port` 为待检查端口。
    返回值：可绑定时返回 True。
    异常情况：无。
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0
