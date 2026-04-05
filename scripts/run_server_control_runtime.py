"""启动服务器控制面参考实现。"""

import argparse
import socket
import sys
from ipaddress import ip_address
from pathlib import Path

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nextgen.apps.server.runtime.http_control_app import build_server_control_app
from nextgen.shared.utils.logging_utils import setup_file_logger


def detect_preferred_ipv4() -> str:
    """探测当前机器优先使用的局域网 IPv4 地址。"""

    def _is_preferred(address: str) -> bool:
        try:
            parsed = ip_address(address)
        except ValueError:
            return False
        if parsed.version != 4:
            return False
        if parsed.is_loopback or parsed.is_link_local:
            return False
        if address.startswith("198.18.") or address.startswith("198.19."):
            return False
        return parsed.is_private

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.connect(("8.8.8.8", 80))
            address = client.getsockname()[0]
            if _is_preferred(address):
                return address
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = sockaddr[0]
            if _is_preferred(address):
                return address
    except OSError:
        pass

    return "127.0.0.1"


def build_argument_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="启动 nextgen 服务器控制面。")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18490)
    parser.add_argument("--log-file", default="nextgen/apps/server/logs/server-runtime.log")
    return parser


def main() -> None:
    """脚本主入口。"""

    args = build_argument_parser().parse_args()
    setup_file_logger("nextgen.server.runtime", args.log_file)
    advertise_host = detect_preferred_ipv4()
    logger = setup_file_logger("nextgen.server.runtime.bootstrap", args.log_file)
    logger.info(
        "启动服务器控制面(server_bootstrap) %s",
        {"host": args.host, "port": args.port, "advertise_host": advertise_host, "status_url": f"http://{advertise_host}:{args.port}/status"},
    )
    uvicorn.run(build_server_control_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
