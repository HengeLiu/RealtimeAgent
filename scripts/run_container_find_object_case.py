"""容器级寻找物体场景执行脚本。"""

import argparse
from pathlib import Path

from nextgen.integration.container_sim.case_runner import (
    run_containerized_find_object_case,
    write_containerized_find_object_report,
)


def build_argument_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="运行容器级寻找物体标准场景。")
    parser.add_argument("--case-id", default="find_object_phone_center_001", help="标准测试场景编号。")
    parser.add_argument("--status-dir", default="/shared/status", help="共享状态目录。")
    parser.add_argument(
        "--output",
        default="/shared/results/find_object_phone_center_001.json",
        help="结果输出文件路径。",
    )
    return parser


def main() -> None:
    """脚本主入口。"""

    parser = build_argument_parser()
    args = parser.parse_args()
    report = run_containerized_find_object_case(
        case_id=args.case_id,
        status_dir=Path(args.status_dir),
    )
    write_containerized_find_object_report(
        output_path=Path(args.output),
        report=report,
    )


if __name__ == "__main__":
    main()
