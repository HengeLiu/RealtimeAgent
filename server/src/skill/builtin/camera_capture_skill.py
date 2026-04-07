from __future__ import annotations

from protocol.enums import SkillMode
from skill.base import SkillBase, SkillRequest, SkillResult


class CameraCaptureSkill(SkillBase):
    name = "camera_capture_skill"
    description = "Issue camera capture command to glass side."
    input_schema = {"type": "object", "properties": {"capture_mode": {"type": "string"}}}
    output_schema = {"type": "object"}
    mode = SkillMode.SYNC

    def execute(self, request: SkillRequest) -> SkillResult:
        capture_mode = request.input.get("capture_mode", "single")
        return SkillResult(
            status="accepted",
            data={"message_name": "camera.capture", "capture_mode": capture_mode},
            summary="device_command_issued",
        )
