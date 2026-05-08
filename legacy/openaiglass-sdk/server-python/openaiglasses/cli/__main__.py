"""允许通过 python -m openaiglasses.cli 启动 SDK 命令。"""

from __future__ import annotations

from openaiglasses.cli.main import main


if __name__ == "__main__":
    raise SystemExit(main())
