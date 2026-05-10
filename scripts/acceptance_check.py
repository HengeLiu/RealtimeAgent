"""audio-chat 并行开发自动验收入口。

本脚本面向多个并行开发小组使用。每个验收组只运行自己负责的最小测试，
避免所有人都依赖完整端到端链路。脚本本身不实现业务逻辑，只负责：

1. 统一工作目录，避免从不同子目录运行得到不同结果。
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


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIO_CHAT_ROOT = REPO_ROOT


@dataclass(frozen=True)
class CheckCommand:
    """单条验收命令。

    主要功能：描述某个开发线路需要执行的命令。
    主要属性：`name` 是报告里的步骤名，`command` 是实际执行命令。
    """

    name: str
    command: tuple[str, ...]
    cwd: Path = AUDIO_CHAT_ROOT


FOUNDATION_CHECKS: dict[str, tuple[CheckCommand, ...]] = {
    "p0-foundation": (
        CheckCommand("unit_all_from_audio_chat", ("uv", "run", "python", "-m", "pytest", "tests", "-q")),
        CheckCommand(
            "unit_all_from_repo_root",
            ("uv", "run", "python", "-m", "pytest", "tests", "-q"),
            cwd=REPO_ROOT,
        ),
        CheckCommand(
            "preflight",
            (
                "uv",
                "run",
                "audio-chat.dev.preflight",
                "--config",
                "app-examples/for-blind-app/server.yaml",
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
        CheckCommand("config_sync_generates_playback", ("uv", "run", "audio-chat.config.sync", "--output-dir", "runs/acceptance/generated")),
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
                "-q",
            ),
        ),
    ),
}

NEXT_STAGE_CHECKS: dict[str, tuple[CheckCommand, ...]] = {
    "developer-usability": (
        CheckCommand(
            "developer_usability_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_cli_developer_workflow.py",
                "tests/test_config_sync.py",
                "tests/test_docs_commands.py",
                "tests/acceptance/test_developer_usable_gate.py",
                "-q",
            ),
        ),
    ),
    "capability-template-playback": (
        CheckCommand(
            "capability_template_playback_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/acceptance/test_capability_template_playback.py",
                "tests/acceptance/test_auto_discovery_developer_contract.py",
                "-q",
            ),
        ),
    ),
    "audio-session-lifecycle": (
        CheckCommand(
            "audio_pipeline_session_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_audio_session_lifecycle.py",
                "tests/test_audio_pipeline_processors.py",
                "tests/acceptance/test_audio_session_contract.py",
                "-q",
            ),
        ),
    ),
    "auth-device-management": (
        CheckCommand(
            "auth_device_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_device_registration_management.py",
                "tests/test_signed_token_auth.py",
                "-q",
            ),
        ),
    ),
    "memory-skill-mcp": (
        CheckCommand(
            "memory_skill_mcp_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_memory_service.py",
                "tests/test_skill_service.py",
                "tests/test_mcp_gateway.py",
                "tests/acceptance/test_indirect_device_context_contract.py",
                "-q",
            ),
        ),
    ),
    "task-engine-production": (
        CheckCommand(
            "task_engine_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_task_engine_persistence.py",
                "tests/test_task_engine_scheduler.py",
                "tests/test_task_signal_bridge.py",
                "tests/acceptance/test_task_device_stream_contract.py",
                "-q",
            ),
        ),
    ),
    "provider-output-runtime": (
        CheckCommand(
            "provider_output_runtime_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_text_agent_tool_loop_async.py",
                "tests/test_realtime_provider_tool_bridge.py",
                "tests/test_streaming_tts_runtime.py",
                "tests/integration/test_dashscope_providers.py",
                "-q",
            ),
        ),
    ),
    "text-route-audiosample": (
        CheckCommand(
            "text_route_audiosample_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_text_route_audio_samples.py",
                "-q",
            ),
        ),
    ),
    "endpoint-reference": (
        CheckCommand(
            "endpoint_reference_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_web_glass_endpoint.py",
                "tests/test_python_phone_mock_endpoint.py",
                "tests/test_endpoint_config_sync.py",
                "-q",
            ),
        ),
    ),
    "esp32-s3-endpoint": (
        CheckCommand(
            "esp32_s3_endpoint_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_esp32_s3_endpoint_contract.py",
                "tests/test_endpoint_config_sync.py",
                "-q",
            ),
        ),
    ),
    "developer-experience": (
        CheckCommand(
            "developer_experience_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_cli_server_process.py",
                "tests/test_package_boundary.py",
                "tests/test_docs_commands.py",
                "-q",
            ),
        ),
    ),
    "next-docs-contract": (
        CheckCommand(
            "next_docs_contract_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/acceptance/test_next_docs_contract.py",
                "tests/acceptance/test_migration_template_contract.py",
                "-q",
            ),
        ),
    ),
    "device-api-upgrade-release": (
        CheckCommand(
            "release_tests",
            (
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                "tests/test_release_package.py",
                "tests/test_package_boundary.py",
                "tests/acceptance/test_release_candidate_gate.py",
                "-q",
            ),
        ),
        CheckCommand(
            "release_package_check",
            (
                "uv",
                "run",
                "audio-chat.sdk.package-check",
                "--report",
                "runs/acceptance/device-api-upgrade-release-package-check.json",
            ),
        ),
        CheckCommand("release_for_blind_config_sync", ("uv", "run", "audio-chat.config.sync", "--output-dir", "runs/acceptance/release-generated")),
    ),
}

CHECKS: dict[str, tuple[CheckCommand, ...]] = {
    **FOUNDATION_CHECKS,
    **NEXT_STAGE_CHECKS,
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
