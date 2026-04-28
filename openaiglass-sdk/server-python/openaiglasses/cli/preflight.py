"""执行 SDK 联调前预检。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import perf_counter
from urllib.request import urlopen


REPO_ROOT = Path.cwd().resolve()
APP_ROOT = REPO_ROOT / "openaiglass-for-blind"
SDK_ROOT = REPO_ROOT / "openaiglass-sdk"

from infra.config import ServerSettings


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
    parser.add_argument("--repo-root", type=str, default=".", help="项目根目录")
    parser.add_argument("--app-root", type=str, default="openaiglass-for-blind", help="业务工程根目录")
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
        "--skip-boundary",
        action="store_true",
        help="跳过 SDK 与业务工程边界检查",
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


def configure_paths(args: argparse.Namespace) -> None:
    """根据命令行参数配置仓库路径。"""

    global REPO_ROOT, APP_ROOT, SDK_ROOT
    REPO_ROOT = Path(args.repo_root).resolve()
    APP_ROOT = Path(args.app_root)
    if not APP_ROOT.is_absolute():
        APP_ROOT = (REPO_ROOT / APP_ROOT).resolve()
    SDK_ROOT = REPO_ROOT / "openaiglass-sdk"
    for path in (APP_ROOT, SDK_ROOT / "server-python", REPO_ROOT):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def run_command(*, name: str, command: list[str]) -> CheckResult:
    """执行一个子命令检查。"""

    start = perf_counter()
    pythonpath_items = [
        str(APP_ROOT),
        str(SDK_ROOT / "server-python"),
        str(SDK_ROOT / "glass-playback"),
        str(SDK_ROOT / "phone-mock"),
        str(REPO_ROOT),
    ]
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    if inherited_pythonpath:
        pythonpath_items.append(inherited_pythonpath)
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(pythonpath_items)},
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

    from host.server.main import create_server_handle

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


def run_video_link_adapter_check() -> CheckResult:
    """检查真实服务端句柄是否已绑定 SDK 视频链路适配器。

    测试目标：
    1. 防止业务宿主继续手动注入 `DeviceGroupRuntime.video_link_*_adapter`。
    2. 确认 `DeviceGroupContext.start_phone_video_link()` 可进入 SDK 系统任务入口。

    测试方法：
    1. 构建业务真实服务端句柄。
    2. 检查 `DeviceGroupRuntime` 上的启动和停止适配器是否存在。

    预期结果：
    1. 两个适配器都由 SDK 控制运行时自动绑定。
    """

    from host.server.main import create_server_handle

    start = perf_counter()
    handle = create_server_handle(ServerSettings(host="127.0.0.1", port=0))
    try:
        runtime = handle.runtime.device_group_runtime
        start_bound = runtime.video_link_start_adapter is not None
        stop_bound = runtime.video_link_stop_adapter is not None
        ok = start_bound and stop_bound
        details = {
            "video_link_start_adapter_bound": start_bound,
            "video_link_stop_adapter_bound": stop_bound,
            "runtime_type": runtime.__class__.__name__,
        }
    except Exception as exc:  # pragma: no cover - 脚本型检查，异常直接写入报告
        ok = False
        details = {"error": str(exc)}
    finally:
        try:
            handle.server.server_close()
        except Exception:
            pass
    duration_ms = int((perf_counter() - start) * 1000)
    return CheckResult(
        name="server_video_link_adapters",
        ok=ok,
        duration_ms=duration_ms,
        details=details,
    )


def run_entrypoint_check() -> CheckResult:
    """检查服务端、手机端、眼镜端联调入口是否存在。"""

    start = perf_counter()
    paths = {
        "server_entry": APP_ROOT / "host/server/main.py",
        "phone_sdk_project": SDK_ROOT / "phone-ios/GlassesVideoReceiver.xcodeproj",
        "glass_sdk_project": SDK_ROOT / "glass-esp32",
        "glass_playback_runtime": SDK_ROOT / "glass-playback/openaiglass_glass_playback",
        "glass_playback_config_dir": APP_ROOT / "host/glass-playback/config",
        "phone_mock_runtime": SDK_ROOT / "phone-mock/openaiglass_phone_mock",
        "phone_mock_config": APP_ROOT / "host/phone-mock/config/phone.mock.json",
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
    """检查 SDK 运行时与盲人业务工程之间的边界。

    测试目标：
    1. 根目录 `server / phone / glass` 不重新承载具体业务能力。
    2. SDK 框架代码不反向依赖盲人业务能力。
    3. iOS 根工程不默认编译具体业务能力文件。

    测试方法：
    1. 扫描根运行时目录中的业务关键词。
    2. 扫描 SDK 与根运行时中的业务能力导入。
    3. 扫描 Xcode 工程是否引用业务能力文件。

    预期结果：
    1. 所有业务实现只出现在 `capabilities/` 或文档测试资产中。
    """

    start = perf_counter()
    violations: list[dict[str, object]] = []

    runtime_roots = [
        APP_ROOT / "host/phone/src",
        SDK_ROOT / "phone-ios/GlassesVideoReceiver",
        SDK_ROOT / "phone-ios/GlassesVideoReceiverTests",
        SDK_ROOT / "phone-ios/GlassesVideoReceiver.xcodeproj/project.pbxproj",
        SDK_ROOT / "glass-esp32",
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
        SDK_ROOT / "server-python",
        APP_ROOT / "host/phone/src",
        SDK_ROOT / "phone-ios/GlassesVideoReceiver",
        SDK_ROOT / "glass-esp32",
    ]
    dependency_patterns = [
        "from capabilities",
        "import capabilities",
        "../../capabilities",
    ]
    for root in dependency_roots:
        for path in _iter_source_files(root):
            if path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            matched = [pattern for pattern in dependency_patterns if pattern in text]
            if matched:
                violations.append(
                    {
                        "type": "runtime_depends_on_capabilities",
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
    configure_paths(args)
    checks: list[CheckResult] = []

    if not args.skip_compile:
        checks.append(
            run_command(
                name="compileall",
                command=[
                    sys.executable,
                    "-m",
                    "compileall",
                    "openaiglass-sdk/server-python",
                    "openaiglass-sdk/glass-playback",
                    "openaiglass-sdk/phone-mock",
                    "openaiglass-for-blind/host/server",
                    "openaiglass-for-blind/host/phone/src",
                    "openaiglass-sdk/phone-ios/GlassesVideoReceiver",
                    "openaiglass-sdk/glass-esp32",
                    "openaiglass-for-blind/capabilities",
                    "openaiglass-sdk/tests/unit",
                    "openaiglass-sdk/server-python/openaiglasses/cli/preflight.py",
                    "openaiglass-sdk/server-python/openaiglasses/cli/live_check.py",
                    "openaiglass-sdk/server-python/openaiglasses/cli/package_check.py",
                    "openaiglass-sdk/server-python/openaiglasses/cli/contract_tests.py",
                ],
            )
        )

    checks.append(run_entrypoint_check())
    checks.append(run_video_link_adapter_check())

    if not args.skip_boundary:
        checks.append(run_boundary_check())

    if not args.skip_package:
        checks.append(
            run_command(
                name="sdk_package",
                command=[
                    sys.executable,
                    "-m",
                    "openaiglasses.cli.package_check",
                    "--repo-root",
                    str(REPO_ROOT),
                ],
            )
        )

    if not args.skip_contracts:
        checks.append(
            run_command(
                name="contract_suite",
                command=[
                    sys.executable,
                    "-m",
                    "openaiglasses.cli.contract_tests",
                    "--repo-root",
                    str(REPO_ROOT),
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
                    "openaiglass-sdk/tests/contracts",
                    "openaiglass-sdk/tests/unit/test_sdk_phase_two.py",
                    "openaiglass-sdk/tests/unit/test_playback_config.py",
                    "openaiglass-sdk/tests/unit/test_agent_core.py",
                    "openaiglass-sdk/tests/unit/test_backend_task_core.py",
                    "openaiglass-sdk/tests/integration/test_control_register_flow.py",
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
