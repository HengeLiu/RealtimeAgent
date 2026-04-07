from __future__ import annotations

from dataclasses import dataclass, field

from protocol.enums import BindingStatus
from protocol.models.device import DeviceBinding


@dataclass(slots=True)
class BindingRegistry:
    _by_glass: dict[str, DeviceBinding] = field(default_factory=dict)

    def upsert(self, binding: DeviceBinding) -> None:
        self._by_glass[binding.glass_device_id] = binding

    def get_by_glass(self, glass_device_id: str) -> DeviceBinding | None:
        return self._by_glass.get(glass_device_id)

    def get_active_phone(self, glass_device_id: str) -> str | None:
        binding = self._by_glass.get(glass_device_id)
        if not binding or binding.status != BindingStatus.ACTIVE:
            return None
        return binding.phone_device_id

    def break_binding(self, glass_device_id: str) -> None:
        binding = self._by_glass.get(glass_device_id)
        if binding:
            binding.status = BindingStatus.BROKEN
