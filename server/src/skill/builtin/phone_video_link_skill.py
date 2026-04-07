from __future__ import annotations

from protocol.enums import SkillMode
from skill.base import SkillBase, SkillRequest, SkillResult


class PhoneVideoLinkSkill(SkillBase):
    name = "phone_video_link_skill"
    description = "Request peer link setup for phone/glass data plane."
    input_schema = {"type": "object"}
    output_schema = {"type": "object"}
    mode = SkillMode.TASK_SPAWN

    def execute(self, request: SkillRequest) -> SkillResult:
        return SkillResult(
            status="accepted",
            data={
                "message_name": "peer.prepare_link",
                "glass_device_id": request.input.get("glass_device_id"),
                "phone_device_id": request.input.get("phone_device_id"),
            },
            summary="task_spawned",
        )
