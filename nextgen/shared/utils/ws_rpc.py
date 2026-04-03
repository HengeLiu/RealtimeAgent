"""轻量任务级 WebSocket RPC 工具。"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Dict

from websockets.sync.client import connect


class WebSocketRpcClient:
    """面向单个任务级端点的长连接 RPC 客户端。

    主要功能：
    - 通过一条 WebSocket 长连接发送请求并等待响应
    - 供眼镜端连接手机端任务级数据面时复用
    """

    def __init__(self, ws_url: str) -> None:
        """初始化客户端。"""

        self.ws_url = ws_url
        self._lock = threading.Lock()
        self._connection = None

    def request(self, path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """发送一次 RPC 请求并返回响应载荷。"""

        if payload is None:
            payload = {}
        request_id = uuid.uuid4().hex
        request_payload = {
            "request_id": request_id,
            "path": path,
            "payload": payload,
        }
        with self._lock:
            return self._request_with_retry(request_payload=request_payload, request_id=request_id)

    def close(self) -> None:
        """关闭底层连接。"""

        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.close()
                except Exception:
                    pass
                self._connection = None

    def _request_with_retry(self, request_payload: Dict[str, Any], request_id: str) -> Dict[str, Any]:
        """发送请求，并在连接失效时重试一次。"""

        for attempt in range(2):
            try:
                connection = self._ensure_connection()
                connection.send(json.dumps(request_payload, ensure_ascii=False))
                raw_response = connection.recv()
                response = json.loads(raw_response)
                if response.get("request_id") != request_id:
                    raise RuntimeError("任务级 WebSocket RPC 响应 request_id 不匹配。")
                if response.get("status") != "ok":
                    raise RuntimeError(str(response.get("error", "unknown_ws_error")))
                return response.get("payload", {})
            except Exception:
                if self._connection is not None:
                    try:
                        self._connection.close()
                    except Exception:
                        pass
                self._connection = None
                if attempt == 1:
                    raise
        raise RuntimeError("任务级 WebSocket RPC 请求失败。")

    def _ensure_connection(self):
        """确保底层连接已建立。"""

        if self._connection is None:
            self._connection = connect(self.ws_url, open_timeout=5, close_timeout=1, max_size=2**20)
        return self._connection


def wait_for_ws_ready(client: WebSocketRpcClient, timeout_sec: float = 10.0, poll_interval_sec: float = 0.2) -> Dict[str, Any]:
    """等待任务级 WebSocket 入口就绪。"""

    deadline = time.time() + timeout_sec
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            return client.request("/health", {})
        except Exception as exc:
            last_error = exc
            time.sleep(poll_interval_sec)
    raise TimeoutError(f"未在 {timeout_sec} 秒内等到任务级 WebSocket 服务就绪: {client.ws_url}; last_error={last_error}")
