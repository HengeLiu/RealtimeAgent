from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlencode


def open_web(argv: list[str] | None = None) -> None:
    """打开 browser-glass 设备示例页面。

    主要逻辑：
    1. 默认解析仓库内 `examples/dev-support/devices/browser-glass/index.html`。
    2. 默认保持旧行为，输出或打开 file URL。
    3. `--serve` 会启动仓库根目录静态服务，供 ES module 正常导入本地 SDK。
    4. 非 `--print-url` 时按当前平台调用系统打开命令。

    参数：`argv` 为命令行参数。
    返回值：无。
    异常情况：页面文件不存在或系统打开命令失败时抛出异常。
    """

    parser = argparse.ArgumentParser(prog="realtime-agent.web.open", description="打开 realtime-agent browser-glass 设备示例")
    parser.add_argument("--path", default="examples/dev-support/devices/browser-glass/index.html", help="browser-glass HTML 路径")
    parser.add_argument("--print-url", action="store_true", help="只打印 file URL，不打开浏览器")
    parser.add_argument("--serve", action="store_true", help="通过本地 HTTP 静态服务打开页面，支持浏览器 ES module 导入")
    parser.add_argument("--host", default="127.0.0.1", help="--serve 使用的监听地址")
    parser.add_argument("--port", type=int, default=8766, help="--serve 使用的固定端口；保持稳定 origin 以复用浏览器缓存和文件授权")
    parser.add_argument("--server-url", default="http://127.0.0.1:8765", help="browser-glass 要连接的 realtime-agent server URL")
    args = parser.parse_args(argv)

    html_path = _resolve_audio_root_path(args.path)
    if not html_path.exists():
        raise FileNotFoundError(str(html_path))
    if args.serve:
        _serve_web(
            html_path=html_path.resolve(),
            host=args.host,
            port=args.port,
            print_url=args.print_url,
            server_url=args.server_url,
        )
        return
    url = html_path.resolve().as_uri()
    if args.print_url:
        print(url)
        return

    _open_url(url)
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


def _serve_web(*, html_path: Path, host: str, port: int, print_url: bool, server_url: str) -> None:
    """启动轻量本地静态服务并打开 browser-glass。

    主要逻辑：服务根目录固定为仓库根目录，使页面中的相对 module import 可以访问
    `devices/typescript`。`--print-url` 模式只打印 HTTP URL 后立即退出，便于测试。
    参数：`html_path` 为页面路径，`host/port` 为监听地址，`print_url` 控制是否只打印，
    `server_url` 为页面连接的 realtime-agent 服务地址。
    返回值：无。
    异常情况：端口占用或路径不在仓库根目录下时抛出异常。
    """

    audio_root = Path(__file__).resolve().parents[3]
    relative = html_path.relative_to(audio_root)
    handler = partial(_QuietStaticHandler, directory=str(audio_root))
    server = ThreadingHTTPServer((host, port), handler)
    actual_host, actual_port = server.server_address[:2]
    url_path = "/".join(quote(part) for part in relative.parts)
    query = urlencode({"server_url": server_url})
    url = f"http://{actual_host}:{actual_port}/{url_path}?{query}"
    if print_url:
        server.server_close()
        print(url)
        return

    print(url)
    _open_url(url)
    try:
        print("serving browser-glass; press Ctrl+C to stop")
        server.serve_forever()
    except KeyboardInterrupt:
        print("browser-glass static server stopped")
    finally:
        server.server_close()


def _open_url(url: str) -> None:
    """按当前平台打开 URL。"""

    if sys.platform == "darwin":
        command = ["open", url]
    elif sys.platform.startswith("linux"):
        command = ["xdg-open", url]
    elif sys.platform.startswith("win"):
        command = ["cmd", "/c", "start", "", url]
    else:
        raise RuntimeError(f"unsupported platform for web open: {sys.platform}")
    subprocess.run(command, check=True)


class _QuietStaticHandler(SimpleHTTPRequestHandler):
    """本地开发静态服务 handler。

    主要功能：复用标准库静态文件服务，同时压掉每次资源请求的访问日志，避免终端被
    module import 刷屏。
    """

    def log_message(self, format: str, *args: object) -> None:
        """忽略 HTTP 访问日志。"""

        return None
