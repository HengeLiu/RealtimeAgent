from __future__ import annotations

from dataclasses import dataclass, field

from protocol.enums import DeviceStatus
from protocol.models.device import Device


@dataclass(slots=True)
class DeviceRegistry:
    _devices: dict[str, Device] = field(default_factory=dict)

    def upsert(self, device: Device) -> None:
        self._devices[device.device_id] = device

    def get(self, device_id: str) -> Device | None:
        return self._devices.get(device_id)

    def update_status(self, device_id: str, status: DeviceStatus, *, last_seen_at: str | None = None) -> None:
        device = self._devices.get(device_id)
        if not device:
            return
        device.status = status
        if last_seen_at:
            device.last_seen_at = last_seen_at

    def all(self) -> list[Device]:
        return list(self._devices.values())

    def online_device_ids(self) -> list[str]:
        return [d.device_id for d in self._devices.values() if d.status in {DeviceStatus.ONLINE, DeviceStatus.BUSY}]
