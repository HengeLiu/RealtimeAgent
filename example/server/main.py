"""官方示例服务端入口。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
for path in (ROOT_DIR / "sdk/python", ROOT_DIR):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from example.capabilities.find_object.phone.processor import YoloFindObjectProcessor
from example.capabilities.find_object.phone.task import FindObjectPhoneTask
from example.capabilities.find_object.scenario import build_find_object_scenario_handler
from example.capabilities.find_object.server.task import FindObjectTask
from example.capabilities.find_object.server.tool import StartFindObjectTool
from infra.config import ServerSettings
from infra.logging import LogContext, configure_root_logger, get_logger, log_debug, log_info
from openaiglasses import OpenAIGlassesSDK


def create_sdk() -> OpenAIGlassesSDK:
    """创建并装配示例 SDK。

    功能：
    1. 创建 SDK 主入口。
    2. 注册找物体 Tool、Task 和手机处理器。
    3. 返回可启动的 SDK 对象。

    参数：
    1. 无。

    返回值：
    1. `OpenAIGlassesSDK`：已注册示例能力的 SDK 对象。

    异常情况：
    1. 注册能力名称为空时由 SDK 抛出异常。
    """

    sdk = OpenAIGlassesSDK()
    sdk.register_tool(StartFindObjectTool())
    sdk.register_task(FindObjectTask())
    sdk.register_phone_processor(YoloFindObjectProcessor())
    sdk.register_phone_task(FindObjectPhoneTask())
    sdk.register_scenario_handler("find_object", build_find_object_scenario_handler())
    return sdk


def parse_args() -> argparse.Namespace:
    """解析示例服务端命令行参数。"""

    parser = argparse.ArgumentParser(description="OpenAI Glasses SDK 官方示例服务端")
    parser.add_argument("--host", type=str, default=None, help="监听地址")
    parser.add_argument("--port", type=int, default=None, help="监听端口")
    return parser.parse_args()


def create_server_handle(settings: ServerSettings):
    """创建基于 SDK 官方示例能力的真实服务端句柄。"""

    return create_sdk().build_server_handle(settings)


def main() -> None:
    """启动示例服务端。

    功能：
    1. 装配 SDK。
    2. 启动服务端运行时入口。

    参数：
    1. 无。

    返回值：
    1. 无。

    异常情况：
    1. `KeyboardInterrupt` 时由 SDK 主入口优雅停止服务。
    """

    args = parse_args()
    settings = ServerSettings.from_env()
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    settings.validate()

    configure_root_logger(settings.log_level, settings.log_file)
    logger = get_logger("example.server")
    log_info(
        logger,
        "SDK 示例服务端启动中",
        LogContext(
            trace_id="bootstrap",
            fields={
                "host": settings.host,
                "port": settings.port,
                "log_level": settings.log_level,
                "log_file": settings.log_file,
            },
        ),
    )
    log_debug(
        logger,
        "SDK 示例服务端配置摘要",
        LogContext(trace_id="bootstrap", fields=settings.summary()),
    )

    create_sdk().run_server(settings)


if __name__ == "__main__":
    main()
