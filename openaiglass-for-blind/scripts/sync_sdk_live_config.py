"""兼容旧入口：转发到 SDK 统一配置同步命令。"""

from __future__ import annotations

import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
SDK_PYTHON_ROOT = REPO_ROOT / "openaiglass-sdk/server-python"

for search_path in (SDK_PYTHON_ROOT, APP_ROOT, REPO_ROOT):
    text = str(search_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from openaiglasses.cli.main import main as openaiglass_main  # noqa: E402


def main() -> int:
    """执行 SDK 统一配置同步命令。

    返回值：
    1. SDK CLI 的进程退出码。
    """

    return openaiglass_main(["config", "sync", "--app-root", str(APP_ROOT), *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
