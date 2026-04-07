from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from protocol.enums import MediaType
from protocol.models.base import Serializable


@dataclass(slots=True)
class MediaModel(Serializable):
    media_id: str
    media_type: MediaType
    codec: str | None = None
    format: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    frame_index: int | None = None
    chunk_index: int | None = None
    is_final: bool | None = None
    captured_at: str | None = None
    payload_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MediaModel":
        return cls(
            media_id=raw["media_id"],
            media_type=MediaType(raw["media_type"]),
            codec=raw.get("codec"),
            format=raw.get("format"),
            sample_rate=raw.get("sample_rate"),
            channels=raw.get("channels"),
            width=raw.get("width"),
            height=raw.get("height"),
            duration_ms=raw.get("duration_ms"),
            frame_index=raw.get("frame_index"),
            chunk_index=raw.get("chunk_index"),
            is_final=raw.get("is_final"),
            captured_at=raw.get("captured_at"),
            payload_ref=raw.get("payload_ref"),
            metadata=dict(raw.get("metadata") or {}),
        )
