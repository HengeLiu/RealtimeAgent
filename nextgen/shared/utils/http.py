"""轻量 HTTP 工具。"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Dict, Optional


def post_json(url: str, payload: Dict[str, Any], timeout_sec: float = 5.0) -> Dict[str, Any]:
    """发送 JSON POST 请求。"""

    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, timeout_sec: float = 5.0) -> Dict[str, Any]:
    """发送 JSON GET 请求。"""

    with urllib.request.urlopen(url, timeout=timeout_sec) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_http_ready(url: str, timeout_sec: float = 10.0, poll_interval_sec: float = 0.2) -> Dict[str, Any]:
    """等待目标 HTTP 服务就绪。"""

    deadline = time.time() + timeout_sec
    last_error: Optional[Exception] = None
    while time.time() < deadline:
        try:
            return get_json(url, timeout_sec=1.0)
        except Exception as exc:  # pragma: no cover
            last_error = exc
            time.sleep(poll_interval_sec)
    raise TimeoutError(f"未在 {timeout_sec} 秒内等到 HTTP 服务就绪: {url}; last_error={last_error}")
