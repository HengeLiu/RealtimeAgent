from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

PROTOCOL_VERSION = "realtime-agent.v1"
SERVER_PRODUCER_ID = "server-main"

class EventName(StrEnum):
    """内置协议事件名。

    主要功能：为开发者提供可补全、可复用的事件名常量，减少手写字符串导致的拼写
    错误。枚举值仍然是字符串，能直接传入内部 `Event` 和 JSON 序列化。
    """

    CONTROL_DEVICE_REGISTER_REQUESTED = "control.device.register.requested"
    CONTROL_DEVICE_REGISTERED = "control.device.registered"
    CONTROL_DEVICE_REGISTER_FAILED = "control.device.register.failed"
    CONTROL_DEVICE_HEARTBEAT_RECEIVED = "control.device.heartbeat.received"
    CONTROL_DEVICE_STATE_CHANGED = "control.device.state.changed"
    COMMAND_REQUESTED = "command.requested"
    COMMAND_ACCEPTED = "command.accepted"
    COMMAND_PROGRESS = "command.progress"
    COMMAND_COMPLETED = "command.completed"
    COMMAND_FAILED = "command.failed"
    CONTROL_USER_WAKE_DETECTED = "control.user.wake.detected"
    CONTROL_USER_DIALOG_CLOSE_REQUESTED = "control.user.dialog.close.requested"
    CONTROL_AUDIO_SESSION_OPEN_REQUESTED = "control.audio_session.open.requested"
    CONTROL_AUDIO_SESSION_OPENED = "control.audio_session.opened"
    CONTROL_AUDIO_SESSION_CLOSE_REQUESTED = "control.audio_session.close.requested"
    CONTROL_AUDIO_SESSION_CLOSED = "control.audio_session.closed"
    CONTROL_AUDIO_SESSION_TURN_IGNORED = "control.audio_session.turn.ignored"
    AUDIO_SPEECH_STARTED = "audio.speech.started"
    AUDIO_SPEECH_STOPPED = "audio.speech.stopped"
    CONTROL_USER_INTERRUPT_DETECTED = "control.user.interrupt.detected"
    VOICE_TURN_IGNORED = "voice.turn.ignored"
    STREAM_INPUT_OPENED = "stream.input.opened"
    STREAM_INPUT_CLOSED = "stream.input.closed"
    STREAM_INPUT_FAILED = "stream.input.failed"
    STREAM_OUTPUT_OPEN_REQUESTED = "stream.output.open.requested"
    STREAM_OUTPUT_CLOSE_REQUESTED = "stream.output.close.requested"
    STREAM_OUTPUT_FINISH_REQUESTED = "stream.output.finish.requested"
    STREAM_OUTPUT_CLOSED = "stream.output.closed"
    STREAM_OUTPUT_CANCEL_REQUESTED = "stream.output.cancel.requested"
    STREAM_OUTPUT_CANCELLED = "stream.output.cancelled"
    STREAM_OUTPUT_STARTED = "stream.output.started"
    STREAM_OUTPUT_FINISHED = "stream.output.finished"
    STREAM_OUTPUT_FAILED = "stream.output.failed"
    STREAM_CONTROL_OPEN_REQUESTED = "stream.control.open.requested"
    STREAM_CONTROL_CONFIGURE_REQUESTED = "stream.control.open.requested"
    STREAM_CONTROL_CLOSE_REQUESTED = "stream.control.close.requested"
    AGENT_RESPONSE_STARTED = "agent.response.started"
    AGENT_RESPONSE_COMPLETED = "agent.response.completed"
    TOOL_CALL_STARTED = "tool.call.started"
    TOOL_CALL_COMPLETED = "tool.call.completed"
    TASK_EVENT_START = "task.event.start"
    TASK_EVENT_PROCESS = "task.event.process"
    TASK_EVENT_STATUS = "task.event.status"
    TASK_EVENT_FINISH = "task.event.finish"
    TASK_EVENT_CANCEL = "task.event.cancel"
    TASK_EVENT_ERROR = "task.event.error"
    TASK_STATE_CHANGED = "task.state.changed"
    SYSTEM_ERROR_RAISED = "system.error.raised"


class EventPattern(StrEnum):
    """内置事件通配模式。

    主要功能：供 SDK 内部生成控制面路由规则，避免重复手写通配字符串。
    枚举值仍然是协议字符串。
    """

    ALL = "*"
    CONTROL_AUDIO_SESSION_ALL = "control.audio_session.*"
    COMMAND_ALL = "command.*"
    STREAM_CONTROL_ALL = "stream.control.*"
    STREAM_INPUT_ALL = "stream.input.*"
    STREAM_OUTPUT_ALL = "stream.output.*"
    TASK_ALL = "task.*"
    SYSTEM_ALL = "system.*"


class StreamType(StrEnum):
    """内置 stream 类型。

    主要功能：统一传感器和执行器 stream 名称，方便 Tool、Task 和端侧注册订阅复用。
    """

    SENSOR_RGB = "sensor.rgb"
    SENSOR_DEPTH = "sensor.depth"
    SENSOR_IMU = "sensor.imu"
    SENSOR_TOF = "sensor.tof"
    ACTUATOR_HAPTIC = "actuator.haptic"


CONTROL_EVENTS = {event.value for event in EventName}

STREAM_TYPES = {stream_type.value for stream_type in StreamType}

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


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def validate_event_name(event_name: str) -> None:
    """校验事件名是否符合公共协议命名规范。"""

    if not isinstance(event_name, str) or not event_name:
        raise ValueError("event_name is required")
    if "*" in event_name or not EVENT_NAME_PATTERN.fullmatch(event_name):
        raise ValueError(f"invalid event_name format: {event_name}")


def validate_control_event_payload(payload: dict[str, Any]) -> None:
    """拒绝把媒体大字节塞进控制事件 payload。"""

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


def validate_event_envelope_dict(data: dict[str, Any]) -> None:
    """校验公共事件信封不含点对点设备字段。"""

    forbidden = FORBIDDEN_EVENT_FIELDS.intersection(data)
    if forbidden:
        raise ValueError(f"event envelope contains forbidden device routing fields: {sorted(forbidden)}")
    validate_event_name(str(data.get("event_name", "")))
    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("event payload must be an object")
    payload_forbidden = FORBIDDEN_EVENT_FIELDS.intersection(payload)
    if payload_forbidden:
        raise ValueError(f"event payload contains forbidden device routing fields: {sorted(payload_forbidden)}")
    validate_control_event_payload(payload)


@dataclass(frozen=True)
class Event:
    event_name: str | EventName
    user_id: str
    producer_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    version: str = PROTOCOL_VERSION
    event_id: str = field(default_factory=lambda: new_id("evt"))
    timestamp_ms: int = field(default_factory=now_ms)
    session_id: str | None = None
    stream_id: str | None = None
    stream_type: str | StreamType | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_name", str(self.event_name))
        if self.stream_type is not None:
            object.__setattr__(self, "stream_type", str(self.stream_type))

    def to_dict(self) -> dict[str, Any]:
        event_name = str(self.event_name)
        validate_event_name(event_name)
        validate_control_event_payload(dict(self.payload))
        data = {
            "version": self.version,
            "event_id": self.event_id,
            "event_name": event_name,
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        validate_event_envelope_dict(data)
        return cls(
            version=data.get("version", PROTOCOL_VERSION),
            event_id=data.get("event_id", new_id("evt")),
            event_name=data["event_name"],
            timestamp_ms=int(data.get("timestamp_ms", now_ms())),
            user_id=data["user_id"],
            producer_id=data["producer_id"],
            session_id=data.get("session_id"),
            stream_id=data.get("stream_id"),
            stream_type=data.get("stream_type"),
            payload=dict(data.get("payload") or {}),
        )


@dataclass(frozen=True)
class StreamFormat:
    codec: str = "pcm16le"
    sample_rate: int = 16000
    channels: int = 1
    chunk_ms: int = 20


@dataclass(frozen=True)
class StreamChunk:
    user_id: str
    session_id: str
    stream_id: str
    stream_type: str
    seq: int
    payload: bytes
    codec: str = "pcm16le"
    sample_rate: int = 16000
    channels: int = 1
    duration_ms: int = 20
    timestamp_ms: int = field(default_factory=now_ms)
    version: str = PROTOCOL_VERSION
    final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class StreamChunkCodec:
    @staticmethod
    def encode(chunk: StreamChunk) -> bytes:
        header = {
            "version": chunk.version,
            "user_id": chunk.user_id,
            "session_id": chunk.session_id,
            "stream_id": chunk.stream_id,
            "stream_type": chunk.stream_type,
            "seq": chunk.seq,
            "timestamp_ms": chunk.timestamp_ms,
            "codec": chunk.codec,
            "sample_rate": chunk.sample_rate,
            "channels": chunk.channels,
            "duration_ms": chunk.duration_ms,
            "payload_size": len(chunk.payload),
            "final": chunk.final,
            "metadata": chunk.metadata,
        }
        header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return len(header_bytes).to_bytes(4, "big") + header_bytes + chunk.payload

    @staticmethod
    def decode(raw: bytes) -> StreamChunk:
        if len(raw) < 4:
            raise ValueError("StreamChunk message too short")
        header_len = int.from_bytes(raw[:4], "big")
        header_end = 4 + header_len
        if header_len <= 0 or header_end > len(raw):
            raise ValueError("StreamChunk header length is invalid")
        header = json.loads(raw[4:header_end].decode("utf-8"))
        payload = raw[header_end:]
        if int(header.get("payload_size", -1)) != len(payload):
            raise ValueError("StreamChunk payload_size mismatch")
        return StreamChunk(
            version=header.get("version", PROTOCOL_VERSION),
            user_id=header["user_id"],
            session_id=header["session_id"],
            stream_id=header["stream_id"],
            stream_type=header["stream_type"],
            seq=int(header["seq"]),
            timestamp_ms=int(header["timestamp_ms"]),
            codec=header["codec"],
            sample_rate=int(header["sample_rate"]),
            channels=int(header["channels"]),
            duration_ms=int(header["duration_ms"]),
            final=bool(header.get("final", False)),
            metadata=dict(header.get("metadata") or {}),
            payload=payload,
        )
