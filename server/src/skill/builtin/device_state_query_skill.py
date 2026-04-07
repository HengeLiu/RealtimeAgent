from __future__ import annotations

from api.session.device_registry import DeviceRegistry
from protocol.enums import SkillMode
from skill.base import SkillBase, SkillRequest, SkillResult


class DeviceStateQuerySkill(SkillBase):
    name = "device_state_query_skill"
    description = "Query current device state from registry."
    input_schema = {"type": "object", "properties": {"device_id": {"type": "string"}}}
    output_schema = {"type": "object"}
    mode = SkillMode.SYNC

    def __init__(self, device_registry: DeviceRegistry) -> None:
        self._device_registry = device_registry

    def execute(self, request: SkillRequest) -> SkillResult:
        device_id = str(request.input.get("device_id", ""))
        device = self._device_registry.get(device_id)
        if not device:
            return SkillResult(status="failed", summary="device_not_found", error={"device_id": device_id})
        return SkillResult(status="completed", data=device.to_dict(), summary="direct_result")
