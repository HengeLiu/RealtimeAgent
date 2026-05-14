from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .events import PROTOCOL_VERSION, now_ms


@dataclass(frozen=True)
class StreamChunk:
    """端侧 stream 二进制帧。

    主要功能：表达 `/ws/stream` 中的 JSON header 和 payload。
    主要属性：`stream_id` 标识一次流，`stream_type` 标识能力类型，`seq` 标识顺序。
    """

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
    """stream chunk 编解码器。

    主要功能：统一多语言 SDK 使用的二进制帧形状：4 字节 big-endian header 长度、
    JSON header、payload bytes。
    """

    @staticmethod
    def encode_header(header: dict[str, Any], payload: bytes) -> bytes:
        """按 header 字典和 payload 编码二进制帧。

        主要逻辑：自动写入 `payload_size`，保持 JSON 紧凑编码。
        参数：`header` 为 stream header 字典，`payload` 为二进制数据。
        返回值：可发送到 WebSocket 的 bytes。
        异常情况：payload 不是 bytes-like 时由 bytes 转换抛错。
        """

        payload_bytes = bytes(payload)
        header_bytes = json.dumps(
            {**header, "payload_size": len(payload_bytes)},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return len(header_bytes).to_bytes(4, "big") + header_bytes + payload_bytes

    @staticmethod
    def decode_header(raw: bytes) -> dict[str, Any]:
        """把二进制帧解码为 header 字典和 payload。

        主要逻辑：读取前 4 字节 header 长度，解析 JSON header，并校验 payload
        实际长度与 `payload_size` 一致。
        参数：`raw` 为 WebSocket 二进制消息。
        返回值：包含 header 字段和 `payload` 的字典。
        异常情况：帧过短、header 长度非法、payload_size 不一致时抛出 `ValueError`。
        """

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
        return {**header, "payload": payload}

    @classmethod
    def encode(cls, chunk: StreamChunk) -> bytes:
        """编码 `StreamChunk` 对象。"""

        return cls.encode_header(
            {
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
                "final": chunk.final,
                "metadata": chunk.metadata,
            },
            chunk.payload,
        )

    @classmethod
    def decode(cls, raw: bytes) -> StreamChunk:
        """解码为 `StreamChunk` 对象。"""

        data = cls.decode_header(raw)
        return StreamChunk(
            version=str(data.get("version") or PROTOCOL_VERSION),
            user_id=str(data["user_id"]),
            session_id=str(data["session_id"]),
            stream_id=str(data["stream_id"]),
            stream_type=str(data["stream_type"]),
            seq=int(data["seq"]),
            timestamp_ms=int(data["timestamp_ms"]),
            codec=str(data["codec"]),
            sample_rate=int(data["sample_rate"]),
            channels=int(data["channels"]),
            duration_ms=int(data["duration_ms"]),
            final=bool(data.get("final", False)),
            metadata=dict(data.get("metadata") or {}),
            payload=bytes(data["payload"]),
        )
