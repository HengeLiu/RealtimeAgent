"""点分形式命令的兼容入口。"""

from __future__ import annotations

import sys

from openaiglasses.cli.main import main


def server_run() -> int:
    """兼容 `openaiglass.server.run` 命令。"""

    return main(["server", "local", "all", *sys.argv[1:]])


def server_start() -> int:
    """兼容 `openaiglass.server.start` 命令。"""

    return main(["server", "local", "start", *sys.argv[1:]])


def server_stop() -> int:
    """兼容 `openaiglass.server.stop` 命令。"""

    return main(["server", "local", "stop", *sys.argv[1:]])


def server_logs() -> int:
    """兼容 `openaiglass.server.logs` 命令。"""

    return main(["server", "local", "logs", *sys.argv[1:]])


def phone_open() -> int:
    """兼容 `openaiglass.phone.open` 命令。"""

    return main(["phone", "open", *sys.argv[1:]])


def phone_build_sim() -> int:
    """兼容 `openaiglass.phone.build-sim` 命令。"""

    return main(["phone", "build-sim", *sys.argv[1:]])


def glass_start() -> int:
    """兼容 `openaiglass.glass.start` 命令。"""

    return main(["glass", "firmware", *sys.argv[1:]])


def glass_build() -> int:
    """兼容 `openaiglass.glass.build` 命令。"""

    return main(["glass", "firmware", "--build-only", *sys.argv[1:]])
