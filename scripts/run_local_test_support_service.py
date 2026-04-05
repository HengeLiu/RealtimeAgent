"""启动本机多进程联调用测试支持服务。"""

import argparse
import sys
from pathlib import Path

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nextgen.integration.test_support.service import build_test_support_app
from nextgen.shared.utils.logging_utils import setup_file_logger


def build_argument_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="启动本机多进程联调用测试支持服务。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18400)
    parser.add_argument("--server-port", type=int, default=18490)
    parser.add_argument("--glass-port", type=int, default=18491)
    parser.add_argument("--log-file", default="nextgen/integration/test_support/logs/test-support.log")
    return parser


def main() -> None:
    """脚本主入口。"""

    args = build_argument_parser().parse_args()
    logger = setup_file_logger("nextgen.test_support", args.log_file)
    app = build_test_support_app(
        server_port=args.server_port,
        glass_port=args.glass_port,
        logger=logger,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
