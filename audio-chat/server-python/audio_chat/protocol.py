from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = "audio-chat.v1"
SERVER_PRODUCER_ID = "server-main"

CONTROL_EVENTS = {
    "control.device.register.requested",
    "control.device.registered",
    "control.device.register.failed",
    "control.device.heartbeat.received",
    "control.device.state.changed",
    "control.user.wake.detected",
    "control.user.dialog.close.requested",
    "control.audio_session.open.requested",
    "control.audio_session.opened",
    "control.audio_session.close.requested",
    "control.audio_session.closed",
    "control.user.interrupt.detected",
    "stream.input.opened",
    "stream.input.closed",
    "stream.input.failed",
    "stream.output.open.requested",
    "stream.output.close.requested",
    "stream.output.closed",
    "stream.output.cancel.requested",
    "stream.output.cancelled",
    "stream.output.started",
    "stream.output.finished",
    "stream.output.failed",
    "stream.control.configure.requested",
    "agent.response.started",
    "agent.response.completed",
    "tool.call.started",
    "tool.call.completed",
    "task.state.changed",
    "system.error.raised",
}

STREAM_TYPES = {
    "sensor.mic",
    "sensor.rgb",
    "sensor.depth",
    "sensor.imu",
    "actuator.speaker",
    "actuator.haptic",
}


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class Subscription:
    event: str
    filter: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Event:
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
        data = {
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
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
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
