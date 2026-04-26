"""执行 SDK 兼容性回归测试。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from time import perf_counter


REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="执行 SDK 兼容性回归测试")
    parser.add_argument(
        "--report",
        type=str,
        default="",
        help="可选 JSON 报告输出路径",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="是否格式化输出 JSON 报告",
    )
    return parser.parse_args()


def main() -> int:
    """脚本主入口。"""

    args = parse_args()
    command = [
        sys.executable,
        "-m",
        "pytest",
        "server/test/contracts/test_sdk_compatibility.py",
        "-q",
    ]
    start = perf_counter()
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    duration_ms = int((perf_counter() - start) * 1000)
    report = {
        "ok": completed.returncode == 0,
        "duration_ms": duration_ms,
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout.strip().splitlines()[-20:],
        "stderr_tail": completed.stderr.strip().splitlines()[-20:],
    }

    text = json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None)
    print(text)

    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = (REPO_ROOT / report_path).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入报告：{report_path}", file=sys.stderr)

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
