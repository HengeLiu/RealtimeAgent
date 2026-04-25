"""执行 SDK 联调前预检。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import perf_counter
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "sdk/python"))
sys.path.insert(0, str(REPO_ROOT / "server/src"))

from example.server.main import create_server_handle  # noqa: E402
from infra.config import ServerSettings  # noqa: E402


@dataclass(slots=True)
class CheckResult:
    """预检步骤结果。"""

    name: str
    ok: bool
    duration_ms: int
    command: list[str] = field(default_factory=list)
    details: dict[str, object] = field(default_factory=dict)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="执行 SDK 联调前预检")
    parser.add_argument(
        "--report",
        type=str,
        default="",
        help="可选 JSON 报告输出路径",
    )
    parser.add_argument(
        "--skip-compile",
        action="store_true",
        help="跳过 compileall 检查",
    )
    parser.add_argument(
        "--skip-scenarios",
        action="store_true",
        help="跳过 scenario 批量回放",
    )
    parser.add_argument(
        "--skip-pytest",
        action="store_true",
        help="跳过核心 pytest 检查",
    )
    parser.add_argument(
        "--skip-health",
        action="store_true",
        help="跳过服务健康检查",
    )
    return parser.parse_args()


def run_command(*, name: str, command: list[str]) -> CheckResult:
    """执行一个子命令检查。"""

    start = perf_counter()
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    duration_ms = int((perf_counter() - start) * 1000)
    return CheckResult(
        name=name,
        ok=completed.returncode == 0,
        duration_ms=duration_ms,
        command=command,
        details={
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout.strip().splitlines()[-20:],
            "stderr_tail": completed.stderr.strip().splitlines()[-20:],
        },
    )


def run_health_check() -> CheckResult:
    """执行服务健康检查。"""

    start = perf_counter()
    handle = create_server_handle(ServerSettings(host="127.0.0.1", port=0))
    try:
        handle.start()
        url = f"http://127.0.0.1:{handle.port}/api/health"
        with urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        ok = payload.get("status") == "ok" and payload.get("service") == "server-api"
        details = {
            "url": url,
            "payload": payload,
        }
    except Exception as exc:  # pragma: no cover - 脚本型检查，异常直接写入报告
        ok = False
        details = {
            "error": str(exc),
        }
    finally:
        try:
            handle.stop()
        except Exception:
            pass
    duration_ms = int((perf_counter() - start) * 1000)
    return CheckResult(
        name="server_health",
        ok=ok,
        duration_ms=duration_ms,
        details=details,
    )


def run_entrypoint_check() -> CheckResult:
    """检查服务端、手机端、眼镜端联调入口是否存在。"""

    start = perf_counter()
    paths = {
        "server_entry": REPO_ROOT / "example/server/main.py",
        "phone_run_script": REPO_ROOT / "example/phone/run.sh",
        "phone_project": REPO_ROOT / "phone/ios/GlassesVideoReceiver.xcodeproj",
        "glass_run_script": REPO_ROOT / "example/glass/run.sh",
        "glass_project": REPO_ROOT / "glass/src",
        "server_run_script": REPO_ROOT / "script/run_server.sh",
        "sdk_live_check_script": REPO_ROOT / "script/run_sdk_live_check.py",
        "sdk_live_check_shell": REPO_ROOT / "script/run_sdk_live_check.sh",
        "sdk_live_config_sync_script": REPO_ROOT / "script/sync_sdk_live_config.py",
        "sdk_live_config_sync_shell": REPO_ROOT / "script/sync_sdk_live_config.sh",
    }
    details = {
        key: {
            "path": str(path),
            "exists": path.exists(),
        }
        for key, path in paths.items()
    }
    ok = all(item["exists"] for item in details.values())
    duration_ms = int((perf_counter() - start) * 1000)
    return CheckResult(
        name="entrypoints",
        ok=ok,
        duration_ms=duration_ms,
        details=details,
    )


def main() -> int:
    """脚本主入口。"""

    args = parse_args()
    checks: list[CheckResult] = []

    if not args.skip_compile:
        checks.append(
            run_command(
                name="compileall",
                command=[
                    sys.executable,
                    "-m",
                    "compileall",
                    "sdk/python/openaiglasses",
                    "example",
                    "server/test/unit",
                    "script/run_sdk_scenario.py",
                    "script/run_sdk_preflight.py",
                    "script/run_sdk_live_check.py",
                    "script/sync_sdk_live_config.py",
                ],
            )
        )

    checks.append(run_entrypoint_check())

    if not args.skip_scenarios:
        checks.append(
            run_command(
                name="scenario_suite",
                command=[
                    sys.executable,
                    "script/run_sdk_scenario.py",
                    "--scenario-dir",
                    "testdata/scenario",
                ],
            )
        )

    if not args.skip_pytest:
        checks.append(
            run_command(
                name="pytest_core",
                command=[
                    sys.executable,
                    "-m",
                    "pytest",
                    "server/test/unit/test_sdk_phase_two.py",
                    "server/test/unit/test_agent_core.py",
                    "server/test/unit/test_backend_task_core.py",
                    "server/test/integration/test_control_register_flow.py",
                    "-q",
                ],
            )
        )

    if not args.skip_health:
        checks.append(run_health_check())

    report = {
        "ok": all(item.ok for item in checks),
        "check_count": len(checks),
        "passed_count": sum(1 for item in checks if item.ok),
        "failed_count": sum(1 for item in checks if not item.ok),
        "checks": [asdict(item) for item in checks],
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = (REPO_ROOT / report_path).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入预检报告：{report_path}", file=sys.stderr)

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
