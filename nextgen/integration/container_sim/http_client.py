"""容器级直连 HTTP 客户端。"""

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


def post_json(base_url: str, path: str, payload: Dict[str, Any], timeout_sec: float = 5.0) -> Dict[str, Any]:
    """向目标服务发送 JSON POST 请求。"""

    request = urllib.request.Request(
        url=f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(base_url: str, path: str, params: Optional[Dict[str, Any]] = None, timeout_sec: float = 5.0) -> Dict[str, Any]:
    """向目标服务发送 JSON GET 请求。"""

    query = ""
    if params:
        query = f"?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(f"{base_url.rstrip('/')}{path}{query}", timeout=timeout_sec) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_http_ready(base_url: str, timeout_sec: float = 10.0, poll_interval_sec: float = 0.1) -> Dict[str, Any]:
    """等待目标 HTTP 服务就绪。"""

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            return get_json(base_url=base_url, path="/health", timeout_sec=1.0)
        except Exception:
            time.sleep(poll_interval_sec)
    raise TimeoutError(f"未在 {timeout_sec} 秒内等到 HTTP 服务就绪: {base_url}")
