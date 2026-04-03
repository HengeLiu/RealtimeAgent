"""模拟运行时容器启动脚本。"""

import argparse
import time
from pathlib import Path

from nextgen.integration.container_sim.runtime_probe import build_runtime_probe_snapshot, write_runtime_probe_snapshot


def build_argument_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="启动模拟运行时容器。")
    parser.add_argument("--runtime", required=True, help="运行时类型，例如 glass、phone、server。")
    parser.add_argument("--device-id", required=True, help="设备标识。")
    parser.add_argument("--status-dir", default="/shared/status", help="共享状态目录。")
    parser.add_argument("--hold-seconds", type=float, default=30.0, help="容器保活秒数。")
    return parser


def main() -> None:
    """脚本主入口。"""

    parser = build_argument_parser()
    args = parser.parse_args()
    status_dir = Path(args.status_dir)
    snapshot = build_runtime_probe_snapshot(
        runtime=args.runtime,
        device_id=args.device_id,
        metadata={"mode": "container_sim_v1"},
    )
    write_runtime_probe_snapshot(status_dir=status_dir, snapshot=snapshot)
    time.sleep(args.hold_seconds)


if __name__ == "__main__":
    main()
