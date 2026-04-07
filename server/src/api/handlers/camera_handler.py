from __future__ import annotations

from infra.clock.system_clock import SystemClock
from protocol.enums import MessageType
from protocol.messages.envelope import Endpoint, Envelope


class CameraHandler:
    def handle(self, envelope: Envelope) -> list[Envelope]:
        if envelope.message_name == "camera.capture":
            return [
                self._event(envelope, "camera.capture_started", {"status": "capturing"}),
                self._event(envelope, "camera.capture_finished", {"status": "captured"}),
            ]

        if envelope.message_name == "image.stream":
            if not envelope.requires_ack:
                return []
            return [
                Envelope(
                    message_id=f"{envelope.message_id}_ack",
                    trace_id=envelope.trace_id,
                    correlation_id=envelope.message_id,
                    message_type=MessageType.ACK,
                    message_name="camera.ack",
                    protocol_version=envelope.protocol_version,
                    source=Endpoint(device_id=envelope.target.device_id, module=envelope.target.module),
                    target=Endpoint(device_id=envelope.source.device_id, module=envelope.source.module),
                    timestamp=SystemClock.now_iso(),
                    payload={"ack_status": "accepted"},
                )
            ]

        if envelope.requires_ack:
            return [
                Envelope(
                    message_id=f"{envelope.message_id}_ack",
                    trace_id=envelope.trace_id,
                    correlation_id=envelope.message_id,
                    message_type=MessageType.ACK,
                    message_name="camera.ack",
                    protocol_version=envelope.protocol_version,
                    source=Endpoint(device_id=envelope.target.device_id, module=envelope.target.module),
                    target=Endpoint(device_id=envelope.source.device_id, module=envelope.source.module),
                    timestamp=SystemClock.now_iso(),
                    payload={"ack_status": "accepted"},
                )
            ]
        return []

    def _event(self, envelope: Envelope, message_name: str, payload: dict[str, object]) -> Envelope:
        return Envelope(
            message_id=f"{envelope.message_id}_{message_name.split('.')[-1]}",
            trace_id=envelope.trace_id,
            correlation_id=envelope.message_id,
            message_type=MessageType.EVENT,
            message_name=message_name,
            protocol_version=envelope.protocol_version,
            source=Endpoint(device_id=envelope.target.device_id, module="sensor-hub"),
            target=Endpoint(device_id=envelope.source.device_id, module=envelope.source.module),
            timestamp=SystemClock.now_iso(),
            payload=payload,
        )
