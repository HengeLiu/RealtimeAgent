from __future__ import annotations

from infra.clock.system_clock import SystemClock
from protocol.enums import MessageType
from protocol.messages.envelope import Endpoint, Envelope


class AudioHandler:
    def handle(self, envelope: Envelope) -> list[Envelope]:
        if not envelope.requires_ack:
            return []
        return [
            Envelope(
                message_id=f"{envelope.message_id}_ack",
                trace_id=envelope.trace_id,
                correlation_id=envelope.message_id,
                message_type=MessageType.ACK,
                message_name="audio.ack",
                protocol_version=envelope.protocol_version,
                source=Endpoint(device_id=envelope.target.device_id, module=envelope.target.module),
                target=Endpoint(device_id=envelope.source.device_id, module=envelope.source.module),
                timestamp=SystemClock.now_iso(),
                payload={"ack_status": "accepted"},
            )
        ]
