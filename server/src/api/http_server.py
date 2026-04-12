"""服务端 HTTP 启动与基础路由。"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from api.ws import ControlRuntime
from api.ws.websocket_transport import handle_control_websocket
from infra.config import ServerSettings
from infra.errors import AppError


class AppHTTPServer(ThreadingHTTPServer):
    """带运行时上下文的 HTTP 服务。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], request_handler_class, runtime: ControlRuntime):
        super().__init__(server_address, request_handler_class)
        self.runtime = runtime


@dataclass(slots=True)
class ServerHandle:
    """服务端运行句柄。

    主要功能：
    1. 对外暴露 `start/stop` 生命周期控制。
    2. 允许测试用例在进程内启动并关闭服务。

    主要属性：
    1. `server`：底层 HTTPServer 实例。
    2. `thread`：服务线程。
    """

    server: AppHTTPServer
    thread: threading.Thread
    runtime: ControlRuntime

    @property
    def host(self) -> str:
        """返回实际监听地址。"""

        return self.server.server_address[0]

    @property
    def port(self) -> int:
        """返回实际监听端口。"""

        return int(self.server.server_address[1])

    def start(self) -> None:
        """启动服务线程。"""

        self.runtime.start()
        self.thread.start()

    def stop(self) -> None:
        """停止服务并等待线程退出。"""

        self.runtime.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def _json_response(handler: BaseHTTPRequestHandler, status: HTTPStatus, body: dict) -> None:
    """发送 JSON 响应。

    参数：
    1. `handler`：请求处理器。
    2. `status`：HTTP 状态码。
    3. `body`：响应字典。
    """

    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(status.value)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def create_http_server(settings: ServerSettings, runtime: ControlRuntime) -> AppHTTPServer:
    """创建 HTTP 服务实例。

    主要逻辑：
    1. 构建请求处理器并注入配置读取函数。
    2. 注册基础路由：`/api/health` 与 `/api/config-summary`。

    参数：
    1. `settings`：服务端配置。

    返回值：
    1. `ThreadingHTTPServer` 实例。
    """

    def _get_summary() -> dict:
        return settings.summary()

    class RequestHandler(BaseHTTPRequestHandler):
        """最小请求处理器。"""

        get_config_summary: Callable[[], dict] = staticmethod(_get_summary)

        def do_GET(self) -> None:  # noqa: N802
            """处理 GET 请求。

            路由：
            1. `/api/health`：健康检查。
            2. `/api/config-summary`：配置摘要。
            """

            if self.path == "/ws/control":
                handle_control_websocket(self, self.server.runtime)
                return

            if self.path == "/api/health":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "service": "server-api",
                    },
                )
                return

            if self.path == "/api/runtime/devices":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "runtime": self.server.runtime.build_runtime_snapshot(),
                    },
                )
                return

            if self.path == "/api/config-summary":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "config": self.get_config_summary(),
                    },
                )
                return

            _json_response(
                self,
                HTTPStatus.NOT_FOUND,
                {
                    "status": "error",
                    "error": {
                        "code": "NOT_FOUND",
                        "message": f"路径不存在: {self.path}",
                        "retryable": False,
                        "details": {},
                    },
                },
            )

        def log_message(self, format: str, *args: object) -> None:
            """覆盖默认日志输出，避免标准错误噪声。"""

            return

    return AppHTTPServer((settings.host, settings.port), RequestHandler, runtime)


def build_server_handle(settings: ServerSettings) -> ServerHandle:
    """构建可启动的服务句柄。

    参数：
    1. `settings`：服务端配置。

    返回值：
    1. `ServerHandle`。

    异常情况：
    1. 底层端口绑定失败会抛出系统异常。
    """

    runtime = ControlRuntime(settings)
    server = create_http_server(settings, runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    return ServerHandle(server=server, thread=thread, runtime=runtime)


def run_forever(settings: ServerSettings) -> None:
    """以前台阻塞方式运行服务。

    参数：
    1. `settings`：服务端配置。

    异常情况：
    1. `KeyboardInterrupt` 会中断循环并优雅退出。
    """

    handle = build_server_handle(settings)
    try:
        handle.start()
        handle.thread.join()
    except KeyboardInterrupt:
        handle.stop()
    except AppError:
        handle.stop()
        raise
