"""容器级直连 WebSocket RPC 客户端。"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict

from websockets.sync.client import connect


class WebSocketRpcClient:
    """面向单个目标端点的长连接 RPC 客户端。"""

    def __init__(self, ws_url: str) -> None:
        self.ws_url = ws_url
        self._local = threading.local()
        self._connections = []
        self._connections_lock = threading.Lock()

    def request(self, path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """通过长连接发送请求并返回响应负载。"""

        if payload is None:
            payload = {}
        request_id = uuid.uuid4().hex
        request_payload = {
            "request_id": request_id,
            "path": path,
            "payload": payload,
        }
        return self._request_with_retry(request_payload=request_payload, request_id=request_id)

    def close(self) -> None:
        """关闭底层连接。"""

        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            try:
                connection.close()
            except Exception:
                continue
        self._local.connection = None

    def _request_with_retry(self, request_payload: Dict[str, Any], request_id: str) -> Dict[str, Any]:
        """发送请求，若连接已失效则重连后重试一次。"""

        for attempt in range(2):
            try:
                connection = self._ensure_connection()
                connection.send(json.dumps(request_payload, ensure_ascii=False))
                raw_response = connection.recv()
                response = json.loads(raw_response)
                if response.get("request_id") != request_id:
                    raise RuntimeError("WebSocket RPC 响应 request_id 不匹配。")
                if response.get("status") != "ok":
                    raise RuntimeError(str(response.get("error", "unknown_ws_error")))
                return response.get("payload", {})
            except Exception:
                self.close()
                if attempt == 1:
                    raise
        raise RuntimeError("WebSocket RPC 请求失败。")

    def _ensure_connection(self):
        """确保底层连接存在。"""

        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = connect(self.ws_url, open_timeout=5, close_timeout=1, max_size=2**20)
            self._local.connection = connection
            with self._connections_lock:
                self._connections.append(connection)
        return connection


@dataclass
class PeerRpcClients:
    """三端 WebSocket 长连接客户端集合。"""

    server: WebSocketRpcClient
    phone: WebSocketRpcClient
    glass: WebSocketRpcClient

    @classmethod
    def from_peer_endpoints(cls, peers) -> "PeerRpcClients":
        """从端点配置构造客户端集合。"""

        return cls(
            server=WebSocketRpcClient(peers.server_ws_url),
            phone=WebSocketRpcClient(peers.phone_ws_url),
            glass=WebSocketRpcClient(peers.glass_ws_url),
        )

    def close(self) -> None:
        """关闭全部长连接。"""

        self.server.close()
        self.phone.close()
        self.glass.close()


def wait_for_ws_ready(ws_url: str, timeout_sec: float = 10.0, poll_interval_sec: float = 0.1) -> Dict[str, Any]:
    """等待目标 WebSocket RPC 服务就绪。"""

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        client = WebSocketRpcClient(ws_url)
        try:
            return client.request("/health", {})
        except Exception:
            time.sleep(poll_interval_sec)
        finally:
            client.close()
    raise TimeoutError(f"未在 {timeout_sec} 秒内等到 WebSocket 服务就绪: {ws_url}")
