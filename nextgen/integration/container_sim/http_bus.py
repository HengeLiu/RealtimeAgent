"""容器级 HTTP 消息总线客户端。"""

import json
import time
import urllib.parse
import urllib.request
from typing import Iterable, List, Optional
from uuid import uuid4

from nextgen.integration.container_sim.file_bus import FileBusMessage


class HttpMessageBus:
    """HTTP 消息总线客户端。

    主要功能：
    - 通过 HTTP 与独立消息总线服务通信
    - 为三端容器提供真正的网络边界
    - 保持与原有总线接口尽量一致，便于服务层复用
    """

    def __init__(self, base_url: str, timeout_sec: float = 5.0) -> None:
        """初始化 HTTP 消息总线客户端。"""

        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    def send_message(
        self,
        source: str,
        target: str,
        message_type: str,
        payload: Optional[dict] = None,
        session_id: Optional[str] = None,
    ) -> FileBusMessage:
        """发送一条消息到 HTTP 总线。"""

        message = FileBusMessage(
            message_id=f"msg_{uuid4().hex}",
            source=source,
            target=target,
            message_type=message_type,
            payload=payload or {},
            session_id=session_id,
        )
        request = urllib.request.Request(
            url=f"{self.base_url}/messages",
            data=json.dumps(message.to_dict(), ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return FileBusMessage(**payload["message"])

    def receive_messages(self, target: str, message_types: Optional[Iterable[str]] = None) -> List[FileBusMessage]:
        """拉取目标运行时的全部待处理消息。"""

        query = [("target", target)]
        for message_type in message_types or []:
            query.append(("message_type", message_type))
        url = f"{self.base_url}/messages?{urllib.parse.urlencode(query, doseq=True)}"
        with urllib.request.urlopen(url, timeout=self.timeout_sec) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return [FileBusMessage(**item) for item in payload["messages"]]

    def wait_for_message(
        self,
        target: str,
        expected_types: Iterable[str],
        timeout_sec: float = 10.0,
        poll_interval_sec: float = 0.1,
    ) -> FileBusMessage:
        """等待目标运行时出现某类消息。"""

        deadline = time.time() + timeout_sec
        expected_list = list(expected_types)
        while time.time() < deadline:
            messages = self.receive_messages(target=target, message_types=expected_list)
            if messages:
                return messages[0]
            time.sleep(poll_interval_sec)
        raise TimeoutError(f"未在 {timeout_sec} 秒内等到目标 {target} 的期望 HTTP 消息。")

    def health_check(self) -> dict:
        """检查 HTTP 总线服务是否存活。"""

        with urllib.request.urlopen(f"{self.base_url}/health", timeout=self.timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))
