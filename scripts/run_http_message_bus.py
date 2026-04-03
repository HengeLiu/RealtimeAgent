"""HTTP 消息总线服务启动脚本。"""

import argparse

from nextgen.integration.container_sim.http_bus_server import run_http_message_bus_server


def build_argument_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="启动容器级 HTTP 消息总线。")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址。")
    parser.add_argument("--port", type=int, default=18080, help="监听端口。")
    return parser


def main() -> None:
    """脚本主入口。"""

    parser = build_argument_parser()
    args = parser.parse_args()
    run_http_message_bus_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
