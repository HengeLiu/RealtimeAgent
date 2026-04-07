from __future__ import annotations

from infra.clock.system_clock import SystemClock
from protocol.enums import MessageType
from protocol.messages.envelope import Endpoint, Envelope


class TaskHandler:
    def handle(self, envelope: Envelope) -> list[Envelope]:
        if envelope.message_name == "task.create":
            return [
                Envelope(
                    message_id=f"{envelope.message_id}_created",
                    trace_id=envelope.trace_id,
                    correlation_id=envelope.message_id,
                    message_type=MessageType.EVENT,
                    message_name="task.created",
                    protocol_version=envelope.protocol_version,
                    source=Endpoint(device_id=envelope.target.device_id, module="backend-task-core"),
                    target=Endpoint(device_id=envelope.source.device_id, module=envelope.source.module),
                    timestamp=SystemClock.now_iso(),
                    payload={"status": "accepted", "task_type": envelope.payload.get("task_type")},
                )
            ]
        return []
