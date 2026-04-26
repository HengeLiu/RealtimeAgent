"""执行 SDK 场景回放并输出报告。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
sys.path.insert(0, str(APP_ROOT))
sys.path.insert(0, str(REPO_ROOT / "openaiglass-sdk/server-python"))

from host.server.main import create_sdk  # noqa: E402
from openaiglasses.testing import ScenarioRunner  # noqa: E402


def resolve_app_path(path_text: str) -> Path:
    """把命令行路径解析到盲人业务工程。

    主要逻辑：
    1. 绝对路径直接返回。
    2. 相对路径优先按仓库根目录解析，兼容带 `openaiglass-for-blind/` 前缀的调用。
    3. 如果仓库根目录下不存在，则按盲人业务工程根目录解析，兼容旧的 `testdata/scenario` 写法。

    参数：
    1. `path_text`：命令行传入的路径。

    返回值：
    1. 解析后的绝对路径。

    异常情况：
    1. 本函数不主动抛异常，后续读取文件时会暴露不存在的问题。
    """

    path = Path(path_text)
    if path.is_absolute():
        return path
    repo_candidate = (REPO_ROOT / path).resolve()
    if repo_candidate.exists():
        return repo_candidate
    return (APP_ROOT / path).resolve()


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="执行 SDK 场景回放")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scenario",
        type=str,
        help="场景 manifest 路径，例如 testdata/scenario/find_object_with_testdata.json",
    )
    group.add_argument(
        "--scenario-dir",
        type=str,
        help="批量执行某个目录下的全部场景 JSON，例如 testdata/scenario",
    )
    group.add_argument(
        "--describe-scenario",
        type=str,
        help="输出单个场景摘要，不执行回放",
    )
    group.add_argument(
        "--list-scenarios",
        type=str,
        help="扫描目录下全部场景并输出摘要列表，不执行回放",
    )
    group.add_argument(
        "--validate-scenario",
        type=str,
        help="校验单个场景 manifest 和资产引用，不执行回放",
    )
    group.add_argument(
        "--validate-scenarios",
        type=str,
        help="批量校验目录下全部场景 manifest 和资产引用，不执行回放",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="fast",
        choices=["fast", "realtime"],
        help="回放模式，fast 为快速回放，realtime 为按时间轴真实等待",
    )
    parser.add_argument(
        "--report",
        type=str,
        default="",
        help="可选报告输出路径，若指定则写入 JSON 文件",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="是否以格式化 JSON 输出结果",
    )
    return parser.parse_args()


def run_scenario(*, scenario_path: str, mode: str) -> dict[str, Any]:
    """执行单个场景。"""

    sdk = create_sdk()
    runner = ScenarioRunner(
        sdk,
        workspace_root=APP_ROOT,
        replay_mode=mode,
    )
    scenario_file = resolve_app_path(scenario_path)
    return runner.run(scenario_file)


def describe_scenario(*, scenario_path: str) -> dict[str, Any]:
    """输出单个场景摘要。"""

    sdk = create_sdk()
    runner = ScenarioRunner(
        sdk,
        workspace_root=APP_ROOT,
    )
    scenario_file = resolve_app_path(scenario_path)
    return runner.describe(scenario_file)


def validate_scenario(*, scenario_path: str) -> dict[str, Any]:
    """校验单个场景 manifest。"""

    sdk = create_sdk()
    runner = ScenarioRunner(
        sdk,
        workspace_root=APP_ROOT,
    )
    scenario_file = resolve_app_path(scenario_path)
    return runner.validate(scenario_file)


def resolve_scenarios(*, scenario: str | None, scenario_dir: str | None) -> list[Path]:
    """解析需要执行的场景列表。"""

    if scenario:
        return [resolve_app_path(scenario)]

    scenario_root = resolve_app_path(str(scenario_dir))
    if not scenario_root.is_dir():
        raise RuntimeError(f"场景目录不存在: {scenario_root}")
    scenario_files = sorted(path for path in scenario_root.rglob("*.json") if path.is_file())
    if not scenario_files:
        raise RuntimeError(f"场景目录下没有 JSON 文件: {scenario_root}")
    return scenario_files


def main() -> int:
    """脚本主入口。"""

    args = parse_args()
    if args.describe_scenario:
        output = describe_scenario(scenario_path=args.describe_scenario)
        print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0

    if args.list_scenarios:
        scenario_files = resolve_scenarios(
            scenario=None,
            scenario_dir=args.list_scenarios,
        )
        output = {
            "scenario_count": len(scenario_files),
            "scenarios": [
                describe_scenario(scenario_path=str(path))
                for path in scenario_files
            ],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0

    if args.validate_scenario:
        output = validate_scenario(scenario_path=args.validate_scenario)
        print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0 if output.get("ok", False) else 1

    if args.validate_scenarios:
        scenario_files = resolve_scenarios(
            scenario=None,
            scenario_dir=args.validate_scenarios,
        )
        results = [
            validate_scenario(scenario_path=str(path))
            for path in scenario_files
        ]
        output = {
            "scenario_count": len(results),
            "passed_count": sum(1 for item in results if item.get("ok", False)),
            "failed_count": sum(1 for item in results if not item.get("ok", False)),
            "results": results,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0 if output["failed_count"] == 0 else 1

    scenario_files = resolve_scenarios(
        scenario=args.scenario,
        scenario_dir=args.scenario_dir,
    )
    results = [
        run_scenario(
            scenario_path=str(path),
            mode=args.mode,
        )
        for path in scenario_files
    ]

    output: dict[str, Any] | list[dict[str, Any]]
    if len(results) == 1:
        output = results[0]
    else:
        output = {
            "scenario_count": len(results),
            "passed_count": sum(1 for item in results if (item.get("assertions") or {}).get("passed", False)),
            "failed_count": sum(1 for item in results if not (item.get("assertions") or {}).get("passed", False)),
            "results": results,
        }

    text = json.dumps(
        output,
        ensure_ascii=False,
        indent=2 if args.pretty else None,
    )
    print(text)

    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = (REPO_ROOT / report_path).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"已写入报告：{report_path}", file=sys.stderr)

    failed_results = [item for item in results if not (item.get("assertions") or {}).get("passed", False)]
    if not failed_results:
        print(f"场景回放通过，共 {len(results)} 个场景。", file=sys.stderr)
        return 0

    print(f"场景回放未通过，共 {len(failed_results)} 个失败场景。", file=sys.stderr)
    for scenario_result in failed_results:
        print(f"- 失败场景：{scenario_result.get('scenario_id')}", file=sys.stderr)
        for item in (scenario_result.get("assertions") or {}).get("failures", []):
            print(f"  - {item}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
