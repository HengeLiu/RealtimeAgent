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
        "--skip-boundary",
        action="store_true",
        help="跳过 SDK 与 example 目录边界检查",
    )
    parser.add_argument(
        "--skip-package",
        action="store_true",
        help="跳过 Python SDK 包构建、安装和导入检查",
    )
    parser.add_argument(
        "--skip-contracts",
        action="store_true",
        help="跳过 SDK 公共契约测试",
    )
    parser.add_argument(
        "--skip-compatibility",
        action="store_true",
        help="跳过官方样例兼容性回归测试",
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


def _iter_source_files(root: Path) -> list[Path]:
    """列出需要参与边界检查的源码文件。"""

    if root.is_file():
        return [root]
    if not root.exists():
        return []
    allowed_suffixes = {
        ".c",
        ".h",
        ".json",
        ".pbxproj",
        ".py",
        ".swift",
    }
    ignored_parts = {
        "__pycache__",
        "build",
        "DerivedData",
        "managed_components",
        ".pytest_cache",
    }
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in ignored_parts for part in path.parts):
            continue
        if path.suffix in allowed_suffixes:
            files.append(path)
    return files


def run_boundary_check() -> CheckResult:
    """检查 SDK 运行时与官方示例之间的边界。

    测试目标：
    1. 根目录 `server / phone / glass` 不重新承载具体业务能力。
    2. SDK 框架代码不反向依赖 `example`。
    3. iOS 根工程不默认编译官方样例能力文件。

    测试方法：
    1. 扫描根运行时目录中的业务关键词。
    2. 扫描 SDK 与根运行时中的 `example` 导入。
    3. 扫描 Xcode 工程是否引用官方样例能力文件。

    预期结果：
    1. 所有业务实现只出现在 `example/` 或文档测试资产中。
    """

    start = perf_counter()
    violations: list[dict[str, object]] = []

    runtime_roots = [
        REPO_ROOT / "server/src",
        REPO_ROOT / "phone/src",
        REPO_ROOT / "phone/ios/GlassesVideoReceiver",
        REPO_ROOT / "phone/ios/GlassesVideoReceiverTests",
        REPO_ROOT / "phone/ios/GlassesVideoReceiver.xcodeproj/project.pbxproj",
        REPO_ROOT / "glass/src",
    ]
    business_patterns = [
        "find_object",
        "FindObject",
        "yolo_find_object",
        "YoloFindObject",
        "start_find_object",
        "timer_manage",
        "map_manage",
        "Amap",
        "navigation_task",
    ]
    for root in runtime_roots:
        for path in _iter_source_files(root):
            text = path.read_text(encoding="utf-8", errors="ignore")
            matched = [pattern for pattern in business_patterns if pattern in text]
            if matched:
                violations.append(
                    {
                        "type": "business_code_in_root_runtime",
                        "path": str(path.relative_to(REPO_ROOT)),
                        "patterns": matched,
                    }
                )

    dependency_roots = [
        REPO_ROOT / "sdk/python",
        REPO_ROOT / "server/src",
        REPO_ROOT / "phone/src",
        REPO_ROOT / "phone/ios/GlassesVideoReceiver",
        REPO_ROOT / "glass/src",
    ]
    dependency_patterns = [
        "from example",
        "import example",
        "example.",
        "../../example",
    ]
    for root in dependency_roots:
        for path in _iter_source_files(root):
            text = path.read_text(encoding="utf-8", errors="ignore")
            matched = [pattern for pattern in dependency_patterns if pattern in text]
            if matched:
                violations.append(
                    {
                        "type": "runtime_depends_on_example",
                        "path": str(path.relative_to(REPO_ROOT)),
                        "patterns": matched,
                    }
                )

    duration_ms = int((perf_counter() - start) * 1000)
    return CheckResult(
        name="sdk_boundary",
        ok=not violations,
        duration_ms=duration_ms,
        details={
            "violation_count": len(violations),
            "violations": violations,
            "runtime_roots": [str(path.relative_to(REPO_ROOT)) for path in runtime_roots],
        },
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
                    "sdk/python",
                    "example",
                    "server/test/unit",
                    "script/run_sdk_scenario.py",
                    "script/run_sdk_contract_tests.py",
                    "script/run_sdk_compatibility_tests.py",
                    "script/run_sdk_preflight.py",
                    "script/run_sdk_package_check.py",
                    "script/run_sdk_live_check.py",
                    "script/sync_sdk_live_config.py",
                ],
            )
        )

    checks.append(run_entrypoint_check())

    if not args.skip_boundary:
        checks.append(run_boundary_check())

    if not args.skip_package:
        checks.append(
            run_command(
                name="sdk_package",
                command=[
                    sys.executable,
                    "script/run_sdk_package_check.py",
                ],
            )
        )

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

    if not args.skip_contracts:
        checks.append(
            run_command(
                name="contract_suite",
                command=[
                    sys.executable,
                    "script/run_sdk_contract_tests.py",
                ],
            )
        )

    if not args.skip_compatibility:
        checks.append(
            run_command(
                name="compatibility_suite",
                command=[
                    sys.executable,
                    "script/run_sdk_compatibility_tests.py",
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
                    "server/test/contracts",
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
