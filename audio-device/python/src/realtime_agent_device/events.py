from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = "realtime-agent.v1"
SERVER_PRODUCER_ID = "server-main"

EVENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
FORBIDDEN_EVENT_FIELDS = {"target_device", "target_device_id", "source_device", "source_device_id"}
MEDIA_PAYLOAD_KEYS = {
    "audio",
    "audio_bytes",
    "audio_base64",
    "image",
    "image_bytes",
    "image_base64",
    "video",
    "video_bytes",
    "video_base64",
    "media",
    "media_bytes",
    "media_base64",
    "payload_bytes",
    "raw_bytes",
}
MAX_CONTROL_PAYLOAD_TEXT_CHARS = 16384


def new_id(prefix: str) -> str:
    """生成端侧本地 ID。

    主要逻辑：使用 UUID 的前 12 位十六进制字符，保持短 ID 便于日志查看。
    参数：`prefix` 为 ID 前缀，例如 `evt`、`stream`。
    返回值：带前缀的字符串 ID。
    异常情况：无。
    """

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_ms() -> int:
    """返回当前毫秒时间戳。

    主要逻辑：把系统时间转换为整数毫秒，供事件信封和 stream header 使用。
    参数：无。
    返回值：Unix epoch 毫秒。
    异常情况：无。
    """

    return int(time.time() * 1000)


def validate_event_name(event_name: str) -> None:
    """校验事件名格式。

    主要逻辑：端侧 SDK 允许业务命令名出现在 payload，但底层 event_name 必须是
    `a.b.c` 形式，不能包含通配符。
    参数：`event_name` 为待校验事件名。
    返回值：无。
    异常情况：事件名为空、非字符串或格式不正确时抛出 `ValueError`。
    """

    if not isinstance(event_name, str) or not event_name:
        raise ValueError("event_name is required")
    if "*" in event_name or not EVENT_NAME_PATTERN.fullmatch(event_name):
        raise ValueError(f"invalid event_name format: {event_name}")


def validate_event_envelope_dict(data: dict[str, Any]) -> None:
    """校验端侧控制事件信封。

    主要逻辑：事件路由由 server 的订阅规则决定，端侧 SDK 不允许通过
    `target_device_id` 等字段绕过路由；媒体大字节也必须走 stream，不允许塞进
    控制事件 payload。
    参数：`data` 为事件信封字典。
    返回值：无。
    异常情况：事件名非法、payload 非对象、包含禁用路由字段或媒体字段时抛出
    `ValueError`。
    """

    forbidden = FORBIDDEN_EVENT_FIELDS.intersection(data)
    if forbidden:
        raise ValueError(f"event envelope contains forbidden device routing fields: {sorted(forbidden)}")
    validate_event_name(str(data.get("event_name") or ""))
    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("event payload must be an object")
    payload_forbidden = FORBIDDEN_EVENT_FIELDS.intersection(payload)
    if payload_forbidden:
        raise ValueError(f"event payload contains forbidden device routing fields: {sorted(payload_forbidden)}")
    validate_control_event_payload(payload)


def validate_control_event_payload(payload: dict[str, Any]) -> None:
    """拒绝控制事件 payload 携带媒体大字节。"""

    def walk(value: Any, path: str) -> None:
        key = path.rsplit(".", 1)[-1]
        if key in MEDIA_PAYLOAD_KEYS:
            raise ValueError(f"control event payload must not contain media bytes: {path}")
        if isinstance(value, (bytes, bytearray, memoryview)):
            raise ValueError(f"control event payload must not contain bytes: {path}")
        if isinstance(value, str) and len(value) > MAX_CONTROL_PAYLOAD_TEXT_CHARS:
            raise ValueError(f"control event payload text is too large: {path}")
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                if not isinstance(child_key, str):
                    raise ValueError(f"control event payload key must be string: {path}")
                walk(child_value, f"{path}.{child_key}" if path else child_key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(payload, "payload")


@dataclass(frozen=True)
class RealtimeAgentEvent:
    """端侧控制事件信封。

    主要功能：表达 `/ws/control` 上发送和接收的公共事件结构。
    主要属性：`event_name` 为协议事件名，`payload` 为事件数据，`stream_id` 和
    `stream_type` 用于 stream 生命周期事件。
    """

    event_name: str
    user_id: str
    producer_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    version: str = PROTOCOL_VERSION
    event_id: str = field(default_factory=lambda: new_id("evt"))
    timestamp_ms: int = field(default_factory=now_ms)
    session_id: str | None = None
    stream_id: str | None = None
    stream_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为控制 WebSocket 可发送的字典。

        主要逻辑：补齐固定信封字段，并跳过值为 None 的可选字段。
        参数：无。
        返回值：事件字典。
        异常情况：事件名或 payload 类型非法时抛出 `ValueError`。
        """

        if not isinstance(self.payload, dict):
            raise ValueError("event payload must be an object")
        data: dict[str, Any] = {
            "version": self.version,
            "event_id": self.event_id,
            "event_name": self.event_name,
            "timestamp_ms": self.timestamp_ms,
            "user_id": self.user_id,
            "producer_id": self.producer_id,
            "payload": self.payload,
        }
        if self.session_id is not None:
            data["session_id"] = self.session_id
        if self.stream_id is not None:
            data["stream_id"] = self.stream_id
        if self.stream_type is not None:
            data["stream_type"] = self.stream_type
        validate_event_envelope_dict(data)
        return data

    def to_json(self) -> str:
        """序列化为紧凑 JSON 字符串。"""

        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RealtimeAgentEvent":
        """从控制 WebSocket 字典解析事件。

        主要逻辑：读取必填字段，缺省协议版本、事件 ID 和时间戳按本地值补齐。
        参数：`data` 为 JSON 解析后的字典。
        返回值：`RealtimeAgentEvent`。
        异常情况：必填字段缺失或事件名非法时抛出 `ValueError`。
        """

        validate_event_envelope_dict(data)
        event_name = str(data.get("event_name") or "")
        user_id = str(data.get("user_id") or "")
        producer_id = str(data.get("producer_id") or "")
        if not user_id or not producer_id:
            raise ValueError("event requires user_id and producer_id")
        payload = data.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError("event payload must be an object")
        return cls(
            version=str(data.get("version") or PROTOCOL_VERSION),
            event_id=str(data.get("event_id") or new_id("evt")),
            event_name=event_name,
            timestamp_ms=int(data.get("timestamp_ms") or now_ms()),
            user_id=user_id,
            producer_id=producer_id,
            session_id=data.get("session_id"),
            stream_id=data.get("stream_id"),
            stream_type=data.get("stream_type"),
            payload=dict(payload),
        )

    @classmethod
    def from_json(cls, text: str) -> "RealtimeAgentEvent":
        """从 JSON 字符串解析事件。"""

        return cls.from_dict(json.loads(text))
