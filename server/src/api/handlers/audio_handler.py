from __future__ import annotations

from infra.clock.system_clock import SystemClock
from protocol.enums import MessageType
from protocol.messages.envelope import Endpoint, Envelope


class AudioHandler:
    def handle(self, envelope: Envelope) -> list[Envelope]:
        if envelope.message_name == "audio.start_record":
            return [self._event(envelope, "audio.record_started", {"status": "recording"})]

        if envelope.message_name == "audio.stop_record":
            return [
                self._event(
                    envelope,
                    "audio.record_finished",
                    {"status": "finished", "audio_ref": "audio://placeholder"},
                )
            ]

        if envelope.message_name == "audio.play":
            return [
                self._event(envelope, "audio.play_started", {"status": "playing"}),
                self._event(envelope, "audio.play_finished", {"status": "finished"}),
            ]

        if envelope.message_name == "audio.stop":
            return [self._event(envelope, "audio.play_interrupted", {"status": "interrupted"})]

        if envelope.message_name == "audio.stream":
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

    def _event(self, envelope: Envelope, message_name: str, payload: dict[str, object]) -> Envelope:
        return Envelope(
            message_id=f"{envelope.message_id}_{message_name.split('.')[-1]}",
            trace_id=envelope.trace_id,
            correlation_id=envelope.message_id,
            message_type=MessageType.EVENT,
            message_name=message_name,
            protocol_version=envelope.protocol_version,
            source=Endpoint(device_id=envelope.target.device_id, module="actuator-hub"),
            target=Endpoint(device_id=envelope.source.device_id, module=envelope.source.module),
            timestamp=SystemClock.now_iso(),
            payload=payload,
        )
