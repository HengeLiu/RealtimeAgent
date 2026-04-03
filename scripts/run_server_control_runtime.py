"""启动服务器控制面参考实现。"""

import argparse
import sys
from pathlib import Path

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nextgen.apps.server.runtime.http_control_app import build_server_control_app
from nextgen.shared.utils.logging_utils import setup_file_logger


def build_argument_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="启动 nextgen 服务器控制面。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18090)
    parser.add_argument("--log-file", default="nextgen/apps/server/logs/server-runtime.log")
    return parser


def main() -> None:
    """脚本主入口。"""

    args = build_argument_parser().parse_args()
    setup_file_logger("nextgen.server.runtime", args.log_file)
    uvicorn.run(build_server_control_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
