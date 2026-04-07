from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from protocol.enums import BindingStatus, DeviceStatus, DeviceType
from protocol.models.base import Serializable


@dataclass(slots=True)
class Device(Serializable):
    device_id: str
    device_type: DeviceType
    protocol_version: str
    capabilities: list[str]
    status: DeviceStatus
    device_name: str | None = None
    device_model: str | None = None
    firmware_version: str | None = None
    last_seen_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Device":
        return cls(
            device_id=raw["device_id"],
            device_type=DeviceType(raw["device_type"]),
            protocol_version=raw["protocol_version"],
            capabilities=list(raw.get("capabilities", [])),
            status=DeviceStatus(raw["status"]),
            device_name=raw.get("device_name"),
            device_model=raw.get("device_model"),
            firmware_version=raw.get("firmware_version"),
            last_seen_at=raw.get("last_seen_at"),
            metadata=dict(raw.get("metadata") or {}),
        )


@dataclass(slots=True)
class DeviceBinding(Serializable):
    binding_id: str
    glass_device_id: str
    phone_device_id: str
    status: BindingStatus
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DeviceBinding":
        return cls(
            binding_id=raw["binding_id"],
            glass_device_id=raw["glass_device_id"],
            phone_device_id=raw["phone_device_id"],
            status=BindingStatus(raw["status"]),
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            metadata=dict(raw.get("metadata") or {}),
        )
