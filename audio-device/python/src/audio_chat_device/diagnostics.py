from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeviceDiagnostics:
    """端侧 SDK 诊断快照。

    主要功能：记录注册状态、事件数量和最近错误，方便和 server runs 产物对照。
    """

    registered: bool = False
    control_state: str = "idle"
    stream_state: str = "idle"
    sent_events: int = 0
    received_events: int = 0
    output_chunks: int = 0
    active_streams: int = 0
    last_event_name: str = ""
    last_error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """返回可 JSON 序列化的诊断快照。"""

        return {
            "registered": self.registered,
            "control_state": self.control_state,
            "stream_state": self.stream_state,
            "sent_events": self.sent_events,
            "received_events": self.received_events,
            "output_chunks": self.output_chunks,
            "active_streams": self.active_streams,
            "last_event_name": self.last_event_name,
            "last_error": self.last_error,
            **self.extra,
        }
