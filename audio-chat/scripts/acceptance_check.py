"""audio-chat 并行开发自动验收入口。

本脚本面向多个并行开发小组使用。每个验收组只运行自己负责的最小测试，
避免所有人都依赖完整端到端链路。脚本本身不实现业务逻辑，只负责：

1. 统一工作目录，避免从仓库根目录和 audio-chat 目录运行得到不同结果。
2. 按开发线路运行对应 pytest、CLI 和轻量契约检查。
3. 输出结构化 JSON 报告，方便开发人员和负责人比较结果。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIO_CHAT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CheckCommand:
    """单条验收命令。

    主要功能：描述某个开发线路需要执行的命令。
    主要属性：`name` 是报告里的步骤名，`command` 是实际执行命令。
    """

    name: str
    command: tuple[str, ...]
    cwd: Path = AUDIO_CHAT_ROOT


CHECKS: dict[str, tuple[CheckCommand, ...]] = {
    "p0-foundation": (
        CheckCommand("unit_all_from_audio_chat", ("uv", "run", "python", "-m", "pytest", "tests", "-q")),
        CheckCommand(
            "unit_all_from_repo_root",
            ("uv", "run", "python", "-m", "pytest", "audio-chat/tests", "-q"),
            cwd=REPO_ROOT,
        ),
        CheckCommand(
            "preflight",
            (
                "uv",
                "run",
                "audio-chat.dev.preflight",
                "--config",
                "examples/minimal/server.yaml",
                "--report",
                "runs/acceptance/p0-preflight.json",
            ),
        ),
    ),
    "protocol-control": (
        CheckCommand(
            "protocol_control_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_protocol_contracts.py",
                "tests/test_control_service.py",
                "tests/acceptance/test_protocol_routing_acceptance.py",
                "-q",
            ),
        ),
    ),
    "stream-asset": (
        CheckCommand(
            "stream_asset_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_stream_and_audio_pipeline.py",
                "tests/test_phase2_assets_and_endpoint.py",
                "-q",
            ),
        ),
    ),
    "tool-task-agent": (
        CheckCommand(
            "tool_task_contract_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/acceptance/test_protocol_native_tool_task_contract.py",
                "tests/test_agent_core_router.py",
                "-q",
            ),
        ),
    ),
    "output-observability": (
        CheckCommand(
            "output_provider_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_phase2_providers_output.py",
                "tests/test_realtime_audio_agent_core.py",
                "-q",
            ),
        ),
    ),
    "endpoint-playback": (
        CheckCommand(
            "network_playback_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_network_server_playback.py",
                "tests/playback/test_python_playback.py",
                "tests/test_web_glass_endpoint.py",
                "-q",
            ),
        ),
        CheckCommand(
            "playback_cli_config",
            ("uv", "run", "audio-chat.playback.glass", "--config", "examples/minimal/playback.yaml"),
        ),
    ),
    "docs-contract": (
        CheckCommand(
            "architecture_acceptance_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/acceptance/test_architecture_design_contract_acceptance.py",
                "tests/acceptance/test_architecture_module_alignment.py",
                "-q",
            ),
        ),
    ),
}


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="运行 audio-chat 并行开发验收检查")
    parser.add_argument(
        "lane",
        choices=sorted([*CHECKS.keys(), "all"]),
        help="要验收的开发线路；all 会串行运行全部线路。",
    )
    parser.add_argument(
        "--report",
        default="runs/acceptance/acceptance-result.json",
        help="JSON 报告输出路径，相对 audio-chat 目录。",
    )
    parser.add_argument("--keep-going", action="store_true", help="某个步骤失败后继续运行后续步骤。")
    return parser.parse_args()


def main() -> int:
    """脚本主入口。"""

    args = parse_args()
    lanes = list(CHECKS.keys()) if args.lane == "all" else [args.lane]
    started = perf_counter()
    results = []
    ok = True
    for lane in lanes:
        for command in CHECKS[lane]:
            result = run_command(lane, command)
            results.append(result)
            ok = ok and result["ok"]
            if not result["ok"] and not args.keep_going:
                break
        if not ok and not args.keep_going:
            break

    report = {
        "ok": ok,
        "lane": args.lane,
        "duration_ms": int((perf_counter() - started) * 1000),
        "audio_chat_root": str(AUDIO_CHAT_ROOT),
        "repo_root": str(REPO_ROOT),
        "results": results,
    }
    report_path = AUDIO_CHAT_ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def run_command(lane: str, check: CheckCommand) -> dict:
    """执行单条验收命令并返回结构化结果。

    参数：
    1. `lane`：所属开发线路。
    2. `check`：待执行命令。

    返回值：
    1. 包含命令、退出码、耗时和输出尾部的字典。

    异常情况：
    1. 命令不存在时由 `subprocess.run` 返回失败结果。
    """

    started = perf_counter()
    completed = subprocess.run(
        list(check.command),
        cwd=check.cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "lane": lane,
        "name": check.name,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "duration_ms": int((perf_counter() - started) * 1000),
        "cwd": str(check.cwd),
        "command": list(check.command),
        "stdout_tail": tail(completed.stdout.splitlines()),
        "stderr_tail": tail(completed.stderr.splitlines()),
    }


def tail(lines: Iterable[str], limit: int = 40) -> list[str]:
    """返回输出尾部，避免报告过大。"""

    data = list(lines)
    return data[-limit:]


if __name__ == "__main__":
    raise SystemExit(main())
