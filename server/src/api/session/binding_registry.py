from __future__ import annotations

from dataclasses import dataclass, field

from protocol.enums import BindingStatus
from protocol.models.device import DeviceBinding


@dataclass(slots=True)
class BindingRegistry:
    _by_glass: dict[str, DeviceBinding] = field(default_factory=dict)
    _glass_by_phone: dict[str, str] = field(default_factory=dict)

    def upsert(self, binding: DeviceBinding) -> None:
        old_glass = self._glass_by_phone.get(binding.phone_device_id)
        if old_glass and old_glass != binding.glass_device_id:
            self._by_glass.pop(old_glass, None)
        self._by_glass[binding.glass_device_id] = binding
        self._glass_by_phone[binding.phone_device_id] = binding.glass_device_id

    def get_by_glass(self, glass_device_id: str) -> DeviceBinding | None:
        return self._by_glass.get(glass_device_id)

    def get_by_phone(self, phone_device_id: str) -> DeviceBinding | None:
        glass_id = self._glass_by_phone.get(phone_device_id)
        if not glass_id:
            return None
        return self._by_glass.get(glass_id)

    def get_active_phone(self, glass_device_id: str) -> str | None:
        binding = self._by_glass.get(glass_device_id)
        if not binding or binding.status != BindingStatus.ACTIVE:
            return None
        return binding.phone_device_id

    def break_binding(self, glass_device_id: str) -> None:
        binding = self._by_glass.get(glass_device_id)
        if binding:
            binding.status = BindingStatus.BROKEN
            self._glass_by_phone.pop(binding.phone_device_id, None)
