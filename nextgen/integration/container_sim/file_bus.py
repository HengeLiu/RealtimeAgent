"""容器级文件总线。"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4


@dataclass
class FileBusMessage:
    """文件总线消息。

    主要功能：
    - 描述容器级模拟中一条通过共享目录传递的消息
    - 统一消息标识、源、目标、类型和负载结构
    """

    message_id: str
    source: str
    target: str
    message_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """将消息转换为字典。"""

        return {
            "message_id": self.message_id,
            "source": self.source,
            "target": self.target,
            "message_type": self.message_type,
            "payload": self.payload,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
        }


class FileMessageBus:
    """文件总线实现。

    主要功能：
    - 在共享目录中为不同运行时维护独立 inbox
    - 支持发送、拉取、等待消息
    - 为容器级模拟提供最小跨容器通信能力
    """

    def __init__(self, root_dir: Path) -> None:
        """初始化文件总线。"""

        self.root_dir = root_dir

    def send_message(
        self,
        source: str,
        target: str,
        message_type: str,
        payload: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> FileBusMessage:
        """发送一条消息到目标 inbox。"""

        message = FileBusMessage(
            message_id=f"msg_{uuid4().hex}",
            source=source,
            target=target,
            message_type=message_type,
            payload=payload or {},
            session_id=session_id,
        )
        self._write_message(message)
        return message

    def _write_message(self, message: FileBusMessage) -> None:
        """将消息写入目标 inbox。"""

        inbox_dir = self.root_dir / message.target
        inbox_dir.mkdir(parents=True, exist_ok=True)
        message_path = inbox_dir / f"{message.timestamp}_{message.message_id}.json"
        temp_path = message_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(message.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(message_path)

    def receive_messages(self, target: str) -> List[FileBusMessage]:
        """拉取目标 inbox 中的全部消息，并在读取后删除文件。"""

        inbox_dir = self.root_dir / target
        if not inbox_dir.exists():
            return []

        messages: List[FileBusMessage] = []
        for path in sorted(inbox_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            messages.append(
                FileBusMessage(
                    message_id=payload["message_id"],
                    source=payload["source"],
                    target=payload["target"],
                    message_type=payload["message_type"],
                    payload=payload.get("payload", {}),
                    session_id=payload.get("session_id"),
                    timestamp=payload["timestamp"],
                )
            )
        return messages

    def wait_for_message(
        self,
        target: str,
        expected_types: Iterable[str],
        timeout_sec: float = 10.0,
        poll_interval_sec: float = 0.1,
    ) -> FileBusMessage:
        """等待目标 inbox 中出现某类消息。"""

        expected = set(expected_types)
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            matched_message = None
            deferred_messages: List[FileBusMessage] = []
            for message in self.receive_messages(target):
                if matched_message is None and message.message_type in expected:
                    matched_message = message
                else:
                    deferred_messages.append(message)
            for deferred in deferred_messages:
                self._write_message(deferred)
            if matched_message is not None:
                return matched_message
            time.sleep(poll_interval_sec)
        raise TimeoutError(f"未在 {timeout_sec} 秒内等到目标 {target} 的期望消息。")
