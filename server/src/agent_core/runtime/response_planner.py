from __future__ import annotations

from dataclasses import dataclass

from infra.clock.system_clock import SystemClock
from infra.idgen import IdGenerator
from protocol import PROTOCOL_VERSION
from protocol.enums import MessageType
from protocol.messages.envelope import Endpoint, Envelope


@dataclass(slots=True)
class ResponsePlanner:
    server_device_id: str = "dev_server_main"

    def build_audio_reply(self, *, trace_id: str, target_device_id: str, text: str) -> Envelope:
        return Envelope(
            message_id=IdGenerator.message_id(),
            trace_id=trace_id,
            message_type=MessageType.COMMAND,
            message_name="audio.play",
            protocol_version=PROTOCOL_VERSION,
            source=Endpoint(device_id=self.server_device_id, module="agent-core"),
            target=Endpoint(device_id=target_device_id, module="actuator-hub"),
            timestamp=SystemClock.now_iso(),
            requires_ack=True,
            payload={
                "play_mode": "normal",
                "interrupt_policy": "allow_interrupt",
                "tts_text": text,
            },
        )
