from __future__ import annotations

import argparse
from pathlib import Path

from .case_schema import load_case, load_suite
from .recorder import RecordOptions, record_case
from .report import write_summary_report
from .runner import run_case_sync, run_suite_sync


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""

    parser = argparse.ArgumentParser(prog="python -m realtime_agent_python_playback_glass")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--server-url", default="http://127.0.0.1:8765")
    run_parser.add_argument("--case")
    run_parser.add_argument("--suite")
    run_parser.add_argument("--runs-root", default="examples/simple-agent-server/runs")
    run_parser.add_argument("--report", default="dev-support/devices/python-playback-glass/reports/report.json")
    run_parser.add_argument("--fail-fast", action="store_true")
    run_parser.add_argument("--keep-runs", action="store_true")
    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--runs-root", required=True)
    record_parser.add_argument("--user-id", required=True)
    record_parser.add_argument("--device-id", required=True)
    record_parser.add_argument("--session-id", default="")
    record_parser.add_argument("--audio", default="")
    record_parser.add_argument("--image", action="append", default=[])
    record_parser.add_argument("--case-id", default="")
    record_parser.add_argument("--name", default="")
    record_parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run(args)
    return _record(args)


def _run(args: argparse.Namespace) -> int:
    """执行 run 子命令。"""

    report_path = Path(args.report).expanduser().resolve()
    if args.case:
        reports = [run_case_sync(load_case(args.case), server_url=args.server_url, runs_root=args.runs_root, report_dir=report_path.parent)]
        suite_id = None
    elif args.suite:
        suite = load_suite(args.suite)
        reports = run_suite_sync(suite, server_url=args.server_url, runs_root=args.runs_root, report_dir=report_path.parent, fail_fast=args.fail_fast)
        suite_id = suite.id
    else:
        raise SystemExit("--case or --suite is required")
    summary = write_summary_report(reports=reports, report_path=report_path, suite_id=suite_id)
    print(f"python-playback-glass: cases={summary['case_count']} passed={summary['passed']} failed={summary['failed']} report={report_path}")
    return 0 if summary["ok"] else 1


def _record(args: argparse.Namespace) -> int:
    """执行 record 子命令。"""

    images = {}
    for item in args.image:
        if "=" not in item:
            raise SystemExit(f"--image must be stream_type=path: {item}")
        stream_type, path = item.split("=", 1)
        images[stream_type] = path
    data = record_case(RecordOptions(runs_root=Path(args.runs_root), user_id=args.user_id, device_id=args.device_id, session_id=args.session_id, audio=args.audio, images=images, case_id=args.case_id, name=args.name, out=Path(args.out)))
    print(f"recorded case {data['id']} -> {Path(args.out).resolve()}")
    return 0
