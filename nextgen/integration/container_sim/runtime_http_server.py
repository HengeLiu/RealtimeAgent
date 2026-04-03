"""容器级直连 HTTP 服务端。"""

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict
from urllib.parse import urlparse

from nextgen.integration.container_sim.services import (
    ContainerGlassService,
    ContainerPhoneService,
    ContainerServerService,
    PeerEndpoints,
)


def build_runtime_http_handler(runtime: str, service, peers: PeerEndpoints) -> type[BaseHTTPRequestHandler]:
    """根据运行时类型构造 HTTP 处理器。"""

    class RuntimeHttpHandler(BaseHTTPRequestHandler):
        """运行时 HTTP 请求处理器。"""

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._write_json(HTTPStatus.OK, {"status": "ok", "runtime": runtime})
                return

            if runtime == "server" and parsed.path == "/sessions/latest":
                latest_session_id = service.latest_session_id
                if latest_session_id is None:
                    self._write_json(HTTPStatus.OK, {"session": None, "task_snapshot": None, "recent_logs": []})
                    return
                self._write_json(HTTPStatus.OK, service.get_session_report(latest_session_id))
                return

            if runtime == "server" and parsed.path.startswith("/sessions/"):
                session_id = parsed.path.split("/")[-1]
                self._write_json(HTTPStatus.OK, service.get_session_report(session_id))
                return

            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            payload = self._read_json_body()

            if runtime == "glass":
                response = self._handle_glass_post(parsed.path, payload)
                self._write_json(HTTPStatus.OK, response)
                return

            if runtime == "phone":
                response = self._handle_phone_post(parsed.path, payload)
                self._write_json(HTTPStatus.OK, response)
                return

            if runtime == "server":
                response = self._handle_server_post(parsed.path, payload)
                self._write_json(HTTPStatus.OK, response)
                return

            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            """关闭默认访问日志，避免容器输出过噪。"""

            return

        def _handle_glass_post(self, path: str, payload: Dict[str, object]) -> Dict[str, object]:
            """处理眼镜端 POST 请求。"""

            if path == "/voice-input":
                return service.handle_voice_input(
                    text=str(payload["text"]),
                    audio_ref=str(payload["audio_ref"]),
                    confidence=float(payload.get("vad_confidence", 0.95)),
                    peers=peers,
                )
            if path == "/capture-request":
                return service.handle_capture_request(payload=payload, peers=peers)
            if path == "/guidance-hint":
                return service.handle_guidance_hint(
                    session_id=str(payload["session_id"]),
                    text=str(payload["text"]),
                    peers=peers,
                )
            if path == "/capture-release":
                return service.handle_capture_release(
                    request_id=str(payload["request_id"]),
                    session_id=str(payload["session_id"]),
                )
            return {"error": "not_found"}

        def _handle_phone_post(self, path: str, payload: Dict[str, object]) -> Dict[str, object]:
            """处理手机端 POST 请求。"""

            if path == "/task-start":
                return service.handle_task_start(
                    session_id=str(payload["session_id"]),
                    target_name=str(payload["target_name"]),
                )
            if path == "/frame-analysis":
                return service.handle_frame_analysis(payload=payload, peers=peers)
            return {"error": "not_found"}

        def _handle_server_post(self, path: str, payload: Dict[str, object]) -> Dict[str, object]:
            """处理服务器端 POST 请求。"""

            if path == "/voice-event":
                return service.handle_voice_event(event=payload["event"], peers=peers)
            if path == "/capture-granted":
                return service.handle_capture_granted(
                    session_id=str(payload["session_id"]),
                    grant=payload["grant"],
                )
            if path == "/task-status":
                session_id = str(payload["session_id"])
                state_payload = {key: value for key, value in payload.items() if key != "session_id"}
                return service.handle_task_status(session_id=session_id, payload=state_payload)
            if path == "/guidance-executed":
                session_id = str(payload["session_id"])
                event_payload = {key: value for key, value in payload.items() if key != "session_id"}
                return service.handle_guidance_executed(session_id=session_id, payload=event_payload)
            if path == "/task-completed":
                session_id = str(payload["session_id"])
                event_payload = {key: value for key, value in payload.items() if key != "session_id"}
                return service.handle_task_completed(session_id=session_id, payload=event_payload, peers=peers)
            if path == "/frame-analysis":
                return service.handle_frame_analysis(payload=payload, peers=peers)
            return {"error": "not_found"}

        def _read_json_body(self) -> Dict[str, object]:
            """读取 JSON 请求体。"""

            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            return json.loads(raw)

        def _write_json(self, status: HTTPStatus, payload: Dict[str, object]) -> None:
            """输出 JSON 响应。"""

            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return RuntimeHttpHandler


def build_runtime_service(runtime: str, device_id: str):
    """构造对应运行时的服务对象。"""

    if runtime == "glass":
        return ContainerGlassService(device_id=device_id)
    if runtime == "phone":
        return ContainerPhoneService(device_id=device_id)
    if runtime == "server":
        return ContainerServerService(device_id=device_id)
    raise ValueError(f"不支持的运行时类型: {runtime}")


def run_runtime_http_server(runtime: str, device_id: str, host: str, port: int, peers: PeerEndpoints) -> None:
    """启动某个运行时的 HTTP 服务。"""

    service = build_runtime_service(runtime=runtime, device_id=device_id)
    handler = build_runtime_http_handler(runtime=runtime, service=service, peers=peers)
    server = ThreadingHTTPServer((host, port), handler)
    try:
        server.serve_forever()
    finally:
        service.stop()
