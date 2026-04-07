from __future__ import annotations

from protocol.enums import SkillMode
from skill.base import SkillBase, SkillRequest, SkillResult


class AudioPlaySkill(SkillBase):
    name = "audio_play_skill"
    description = "Issue audio play command to actuator hub."
    input_schema = {"type": "object", "properties": {"tts_text": {"type": "string"}}}
    output_schema = {"type": "object"}
    mode = SkillMode.SYNC

    def execute(self, request: SkillRequest) -> SkillResult:
        text = request.input.get("tts_text", "")
        return SkillResult(
            status="accepted",
            data={"message_name": "audio.play", "tts_text": text},
            summary="device_command_issued",
        )
