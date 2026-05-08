"""OpenAI Glasses SDK 统一命令行。"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """SDK 根命令入口。

    功能：
    1. 提供 `server`、`phone`、`glass` 三类通用设备命令。
    2. 让业务工程通过参数和配置文件注入自身默认路径。

    参数：
    1. `argv`：命令行参数，不包含程序名。

    返回值：
    1. 进程退出码。
    """

    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print_usage()
        return 0
    command = args.pop(0)
    try:
        if command == "server":
            from openaiglasses.cli.server import main as server_main

            return server_main(args)
        if command == "phone":
            from openaiglasses.cli.phone import main as phone_main

            return phone_main(args)
        if command == "glass":
            from openaiglasses.cli.glass import main as glass_main

            return glass_main(args)
        if command == "config":
            from openaiglasses.cli.config import main as config_main

            return config_main(args)
    except Exception as exc:
        print(f"openaiglass 命令执行失败: {exc}", file=sys.stderr)
        return 1
    print(f"Unknown command: {command}", file=sys.stderr)
    print_usage()
    return 2


def print_usage() -> None:
    """打印根命令帮助。"""

    print(
        """Usage:
  openaiglass.server.start [--app-module host.server.main --app-root openaiglass-for-blind]
  openaiglass.server.run [--app-module host.server.main --app-root openaiglass-for-blind]
  openaiglass.config.sync [--app-root openaiglass-for-blind]
  openaiglass.phone.open [--app-root openaiglass-for-blind]
  openaiglass.phone.mock --config <phone-mock.json>
  openaiglass.glass.start [--repo-root .]
  openaiglass.glass.start --runtime playback --config <glass-playback.json>

Examples:
  openaiglass.config.sync --app-root openaiglass-for-blind
  openaiglass.server.start --app-module host.server.main --app-root openaiglass-for-blind
  openaiglass.phone.open --app-root openaiglass-for-blind
  openaiglass.phone.mock --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json
  openaiglass.glass.start --build-only --repo-root .
  openaiglass.glass.start --runtime playback --config openaiglass-for-blind/host/glass-playback/config/glass.water_cup.json
"""
    )
