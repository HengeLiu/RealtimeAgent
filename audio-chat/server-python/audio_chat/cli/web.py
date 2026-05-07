from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def open_web(argv: list[str] | None = None) -> None:
    """打开 web-glass 参考端侧页面。

    主要逻辑：
    1. 默认解析仓库内 `endpoints/web-glass/index.html`。
    2. `--print-url` 只输出 file URL，供测试和无桌面环境使用。
    3. 非 `--print-url` 时按当前平台调用系统打开命令。

    参数：`argv` 为命令行参数。
    返回值：无。
    异常情况：页面文件不存在或系统打开命令失败时抛出异常。
    """

    parser = argparse.ArgumentParser(prog="audio-chat.web.open", description="打开 audio-chat web-glass 参考端侧")
    parser.add_argument("--path", default="endpoints/web-glass/index.html", help="web-glass HTML 路径")
    parser.add_argument("--print-url", action="store_true", help="只打印 file URL，不打开浏览器")
    args = parser.parse_args(argv)

    html_path = _resolve_audio_root_path(args.path)
    if not html_path.exists():
        raise FileNotFoundError(str(html_path))
    url = html_path.resolve().as_uri()
    if args.print_url:
        print(url)
        return

    if sys.platform == "darwin":
        command = ["open", url]
    elif sys.platform.startswith("linux"):
        command = ["xdg-open", url]
    elif sys.platform.startswith("win"):
        command = ["cmd", "/c", "start", "", url]
    else:
        raise RuntimeError(f"unsupported platform for web open: {sys.platform}")
    subprocess.run(command, check=True)
    print(url)


def _resolve_audio_root_path(raw: str) -> Path:
    path = Path(raw)
    if path.exists():
        return path
    audio_root = Path(__file__).resolve().parents[3]
    candidate = audio_root / raw
    if candidate.exists():
        return candidate
    return path
