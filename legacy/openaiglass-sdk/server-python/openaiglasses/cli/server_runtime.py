"""服务端统一运行入口。"""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable

from infra.config import ServerSettings
from infra.logging import LogContext, configure_root_logger, get_logger, log_debug, log_info


def build_parser() -> argparse.ArgumentParser:
    """构建服务端运行入口参数解析器。"""

    parser = argparse.ArgumentParser(prog="python -m openaiglasses.cli.server_runtime", description="运行服务端 app module")
    parser.add_argument("--app-module", required=True, help="服务端入口模块")
    parser.add_argument("--host", type=str, default=None, help="监听地址")
    parser.add_argument("--port", type=int, default=None, help="监听端口")
    return parser


def load_server_factory(app_module: str) -> Callable[[ServerSettings], object]:
    """加载业务模块提供的服务端句柄工厂。"""

    module = importlib.import_module(app_module)
    factory = getattr(module, "create_server_handle", None)
    if not callable(factory):
        raise RuntimeError(f"{app_module} 必须导出 create_server_handle(settings)")
    return factory


def main(argv: list[str] | None = None) -> int:
    """运行服务端模块。"""

    args = build_parser().parse_args(argv)
    settings = ServerSettings.from_env()
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    settings.validate()

    configure_root_logger(settings.log_level, settings.log_file)
    logger = get_logger("openaiglasses.server_runtime")
    log_info(
        logger,
        "服务端启动中",
        LogContext(
            trace_id="bootstrap",
            fields={
                "app_module": args.app_module,
                "host": settings.host,
                "port": settings.port,
                "log_level": settings.log_level,
                "log_file": settings.log_file,
            },
        ),
    )
    log_debug(
        logger,
        "服务端配置摘要",
        LogContext(trace_id="bootstrap", fields=settings.summary()),
    )

    handle = load_server_factory(args.app_module)(settings)
    try:
        handle.start()
        handle.thread.join()
    except KeyboardInterrupt:
        handle.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
