"""服务端主入口。"""

from __future__ import annotations

import argparse

from api import run_forever
from infra.config import ServerSettings
from infra.errors import AppError
from infra.logging import LogContext, configure_root_logger, get_logger, log_info


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    主要逻辑：
    1. 允许通过命令行覆盖监听地址与端口。

    返回值：
    1. 命令行参数对象。
    """

    parser = argparse.ArgumentParser(description="OpenAI 眼镜服务端入口")
    parser.add_argument("--host", type=str, default=None, help="监听地址")
    parser.add_argument("--port", type=int, default=None, help="监听端口")
    return parser.parse_args()


def main() -> None:
    """程序主函数。

    主要逻辑：
    1. 加载配置并初始化日志。
    2. 打印配置摘要。
    3. 启动 HTTP 服务。

    异常情况：
    1. 配置非法时打印结构化错误并退出。
    """

    args = parse_args()
    settings = ServerSettings.from_env()
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    settings.validate()

    configure_root_logger(settings.log_level)
    logger = get_logger("server.main")
    log_info(
        logger,
        "服务端启动中",
        LogContext(trace_id="bootstrap", session_id="phase-b"),
    )
    log_info(
        logger,
        f"配置摘要: {settings.summary()}",
        LogContext(trace_id="bootstrap", session_id="phase-b"),
    )

    run_forever(settings)


if __name__ == "__main__":
    try:
        main()
    except AppError as exc:
        print(exc.to_dict())
        raise SystemExit(2) from exc
