"""容器级 HTTP 消息总线服务。"""

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse


class InMemoryMessageStore:
    """内存消息存储。

    主要功能：
    - 为 HTTP 总线服务维护按 target 分组的消息队列
    - 支持原子写入和按目标读取
    """

    def __init__(self) -> None:
        """初始化内存消息存储。"""

        self._messages: Dict[str, List[dict]] = {}
        self._lock = threading.Lock()

    def push(self, message: dict) -> dict:
        """写入一条消息。"""

        with self._lock:
            self._messages.setdefault(message["target"], []).append(message)
        return message

    def drain(self, target: str, message_types: Optional[List[str]] = None) -> List[dict]:
        """按目标读取并移除消息。"""

        with self._lock:
            queued = self._messages.get(target, [])
            if not queued:
                return []
            if not message_types:
                self._messages[target] = []
                return list(queued)

            matched: List[dict] = []
            remained: List[dict] = []
            allowed = set(message_types)
            for item in queued:
                if item["message_type"] in allowed:
                    matched.append(item)
                else:
                    remained.append(item)
            self._messages[target] = remained
            return matched


class HttpMessageBusHandler(BaseHTTPRequestHandler):
    """HTTP 消息总线请求处理器。"""

    store = InMemoryMessageStore()

    def do_GET(self) -> None:  # noqa: N802
        """处理 GET 请求。"""

        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return

        if parsed.path == "/messages":
            query = parse_qs(parsed.query)
            target = query.get("target", [None])[0]
            if target is None:
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": "missing_target"})
                return
            messages = self.store.drain(target=target, message_types=query.get("message_type"))
            self._write_json(HTTPStatus.OK, {"messages": messages})
            return

        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        """处理 POST 请求。"""

        parsed = urlparse(self.path)
        if parsed.path != "/messages":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        message = self.store.push(payload)
        self._write_json(HTTPStatus.CREATED, {"message": message})

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        """关闭默认访问日志，避免容器输出过噪。"""

        return

    def _write_json(self, status: HTTPStatus, payload: dict) -> None:
        """输出 JSON 响应。"""

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_http_message_bus_server(host: str, port: int) -> None:
    """启动 HTTP 消息总线服务。"""

    server = ThreadingHTTPServer((host, port), HttpMessageBusHandler)
    server.serve_forever()
