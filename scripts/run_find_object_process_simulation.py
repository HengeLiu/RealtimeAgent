"""寻找物体三进程模拟启动脚本。"""

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nextgen.integration.smoke.cli import format_simulation_report


def build_argument_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。

    返回值：
    - 已配置默认场景编号的参数解析器
    """

    parser = argparse.ArgumentParser(
        description="运行寻找物体三进程模拟场景。",
    )
    parser.add_argument(
        "--case-id",
        default="find_object_phone_center_001",
        help="标准测试场景编号。",
    )
    return parser


def main() -> None:
    """脚本主入口。"""

    parser = build_argument_parser()
    args = parser.parse_args()
    print(format_simulation_report(case_id=args.case_id))


if __name__ == "__main__":
    main()
