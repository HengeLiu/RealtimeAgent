from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from protocol.enums import AckStatus, Priority
from protocol.models.device import Device


@dataclass(slots=True)
class RegisterPayload:
    device: Device
    auth: dict[str, Any]
    network: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RegisterPayload":
        return cls(
            device=Device.from_dict(raw["device"]),
            auth=dict(raw.get("auth") or {}),
            network=dict(raw.get("network") or {}),
        )


@dataclass(slots=True)
class HeartbeatPayload:
    device_status: str
    battery_level: int | None = None
    active_task_ids: list[str] = field(default_factory=list)
    connection_quality: str | None = None


@dataclass(slots=True)
class TaskCreatePayload:
    task_type: str
    input: dict[str, Any] = field(default_factory=dict)
    priority: Priority = Priority.NORMAL
    target_device_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AudioPlayPayload:
    play_mode: str
    interrupt_policy: str
    audio_ref: str | None = None
    audio_data: str | None = None
    tts_text: str | None = None


@dataclass(slots=True)
class CameraCapturePayload:
    capture_mode: str
    return_mode: str
    resolution: str | None = None


@dataclass(slots=True)
class PeerLinkPayload:
    link_id: str
    peer_role: str
    transport: str
    media_type: str
    link_params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AckPayload:
    acked_message_id: str
    ack_status: AckStatus
    reason: str | None = None
