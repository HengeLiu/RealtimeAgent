"""服务端 HTTP 启动与基础路由。"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from agent_core import AgentFacade
from api.ws import ControlRuntime
from api.ws.websocket_transport import handle_audio_websocket, handle_control_websocket
from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode
from runtime.voice_runtime import SpeechRecognitionClient, VoiceModelClient


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

        protocol_version = "HTTP/1.1"
        get_config_summary: Callable[[], dict] = staticmethod(_get_summary)

        def do_GET(self) -> None:  # noqa: N802
            """处理 GET 请求。

            路由：
            1. `/api/health`：健康检查。
            2. `/api/config-summary`：配置摘要。
            """

            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)

            if path == "/ws/control":
                handle_control_websocket(self, self.server.runtime)
                return
            if path in {"/ws_audio", "/ws_realtime_audio"}:
                handle_audio_websocket(self, self.server.runtime, query)
                return
            if path == "/stream.wav":
                self.server.runtime.voice_runtime.stream_playback(
                    self,
                    device_id=(query.get("device_id") or [""])[0].strip(),
                    stream_id=(query.get("stream_id") or [""])[0].strip(),
                )
                return

            if path == "/api/health":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "service": "server-api",
                    },
                )
                return

            if path == "/api/runtime/devices":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "runtime": self.server.runtime.build_runtime_snapshot(),
                    },
                )
                return

            if path == "/api/agent/session":
                session_id = (query.get("session_id") or [""])[0].strip()
                if not session_id:
                    _json_response(
                        self,
                        HTTPStatus.BAD_REQUEST,
                        {
                            "status": "error",
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": "缺少 session_id",
                                "retryable": False,
                                "details": {},
                            },
                        },
                    )
                    return
                session_snapshot = self.server.runtime.voice_runtime.agent_facade.get_session_debug_snapshot(session_id)
                if session_snapshot is None:
                    _json_response(
                        self,
                        HTTPStatus.NOT_FOUND,
                        {
                            "status": "error",
                            "error": {
                                "code": "SESSION_NOT_FOUND",
                                "message": f"未找到会话: {session_id}",
                                "retryable": False,
                                "details": {"session_id": session_id},
                            },
                        },
                    )
                    return
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "session": session_snapshot,
                    },
                )
                return

            if path == "/api/config-summary":
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
                        "message": f"路径不存在: {path}",
                        "retryable": False,
                        "details": {},
                    },
                },
            )

        def do_POST(self) -> None:  # noqa: N802
            """处理 POST 请求。

            路由：
            1. `/api/debug/phone-video-link/start`：手动启动眼镜到手机的视频直连任务。
            2. `/api/debug/phone-video-link/stop`：手动停止眼镜到手机的视频直连任务。
            3. `/api/tasks/report-event`：接收手机端通用任务事件。
            """

            parsed = urlsplit(self.path)
            path = parsed.path

            if path == "/api/debug/phone-video-link/start":
                try:
                    body = self._read_json_body()
                    glass_device_id = str(body.get("glass_device_id", "")).strip()
                    target_ws_uri = str(body.get("target_ws_uri", "")).strip()
                    frame_interval_ms = int(body.get("frame_interval_ms", 500))
                    reason = str(body.get("reason", "manual_debug")).strip() or "manual_debug"
                    runtime = self.server.runtime.start_phone_video_link_debug(
                        glass_device_id=glass_device_id,
                        target_ws_uri=target_ws_uri,
                        frame_interval_ms=frame_interval_ms,
                        reason=reason,
                    )
                except AppError as exc:
                    _json_response(
                        self,
                        HTTPStatus.BAD_REQUEST,
                        {
                            "status": "error",
                            "error": exc.to_dict(),
                        },
                    )
                    return
                except ValueError:
                    _json_response(
                        self,
                        HTTPStatus.BAD_REQUEST,
                        {
                            "status": "error",
                            "error": {
                                "code": "INVALID_MESSAGE",
                                "message": "frame_interval_ms 必须是整数",
                                "retryable": False,
                                "details": {},
                            },
                        },
                    )
                    return

                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "task": {
                            "task_id": runtime.task_id,
                            "task_type": runtime.task_type,
                            "state": runtime.state,
                            "device_id": runtime.device_id,
                            "session_id": runtime.session_id,
                            "target_ws_uri": runtime.input.get("target_ws_uri"),
                            "frame_interval_ms": runtime.input.get("frame_interval_ms"),
                            "context": dict(runtime.context),
                        },
                    },
                )
                return

            if path == "/api/debug/phone-video-link/stop":
                try:
                    body = self._read_json_body()
                    glass_device_id = str(body.get("glass_device_id", "")).strip()
                    runtime = self.server.runtime.stop_phone_video_link_debug(
                        glass_device_id=glass_device_id,
                    )
                except AppError as exc:
                    _json_response(
                        self,
                        HTTPStatus.BAD_REQUEST,
                        {
                            "status": "error",
                            "error": exc.to_dict(),
                        },
                    )
                    return

                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "task": {
                            "task_id": runtime["task_id"],
                            "task_type": runtime["task_type"],
                            "state": runtime["state"],
                            "device_id": runtime["device_id"],
                            "session_id": runtime["session_id"],
                            "noop": runtime["noop"],
                        },
                    },
                )
                return

            if path == "/api/tasks/report-event":
                try:
                    body = self._read_json_body()
                    runtime = self.server.runtime.report_task_event(
                        task_id=str(body.get("task_id", "")).strip(),
                        phone_device_id=str(body.get("phone_device_id", "")).strip(),
                        event_name=str(body.get("event_name", "")).strip(),
                        payload=dict(body.get("payload") or {}),
                    )
                except AppError as exc:
                    _json_response(
                        self,
                        HTTPStatus.BAD_REQUEST,
                        {
                            "status": "error",
                            "error": exc.to_dict(),
                        },
                    )
                    return
                except (TypeError, ValueError):
                    _json_response(
                        self,
                        HTTPStatus.BAD_REQUEST,
                        {
                            "status": "error",
                            "error": {
                                "code": "INVALID_MESSAGE",
                                "message": "任务事件字段类型非法",
                                "retryable": False,
                                "details": {},
                            },
                        },
                    )
                    return

                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "task": {
                            "task_id": runtime.task_id,
                            "task_type": runtime.task_type,
                            "state": runtime.state,
                            "device_id": runtime.device_id,
                            "session_id": runtime.session_id,
                            "context": dict(runtime.context),
                        },
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
                        "message": f"路径不存在: {path}",
                        "retryable": False,
                        "details": {},
                    },
                },
            )

        def _read_json_body(self) -> dict:
            """读取并解析 JSON 请求体。

            主要逻辑：
            1. 根据 `Content-Length` 读取请求体。
            2. 执行 JSON 反序列化。
            3. 校验顶层对象必须是字典。

            返回值：
            1. 解析成功后的字典对象。

            异常情况：
            1. 长度非法、JSON 非法或顶层不是对象时抛出结构化错误。
            """

            content_length = int(self.headers.get("Content-Length", "0") or "0")
            if content_length <= 0:
                raise AppError(
                    code=ErrorCode.INVALID_MESSAGE,
                    message="请求体不能为空",
                    retryable=False,
                    details={},
                )
            raw = self.rfile.read(content_length)
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AppError(
                    code=ErrorCode.INVALID_MESSAGE,
                    message="请求体不是合法 JSON",
                    retryable=False,
                    details={"reason": str(exc)},
                ) from exc
            if not isinstance(body, dict):
                raise AppError(
                    code=ErrorCode.INVALID_MESSAGE,
                    message="请求体顶层必须是 JSON 对象",
                    retryable=False,
                    details={},
                )
            return body

        def log_message(self, format: str, *args: object) -> None:
            """覆盖默认日志输出，避免标准错误噪声。"""

            return

    return AppHTTPServer((settings.host, settings.port), RequestHandler, runtime)


def build_server_handle(
    settings: ServerSettings,
    *,
    model_client: VoiceModelClient | None = None,
    asr_client: SpeechRecognitionClient | None = None,
    agent_facade: AgentFacade | None = None,
) -> ServerHandle:
    """构建可启动的服务句柄。

    参数：
    1. `settings`：服务端配置。

    返回值：
    1. `ServerHandle`。

    异常情况：
    1. 底层端口绑定失败会抛出系统异常。
    """

    runtime = ControlRuntime(
        settings,
        model_client=model_client,
        asr_client=asr_client,
        agent_facade=agent_facade,
    )
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
