from __future__ import annotations

from dataclasses import dataclass

from api.router.message_router import MessageRouter
from api.session.connection_manager import ConnectionManager
from api.session.connection_session import Transport
from infra.clock.system_clock import SystemClock
from infra.logging import create_logger
from protocol.codec.json_codec import JsonMessageCodec
from protocol.enums import MessageType
from protocol.messages.envelope import Endpoint, Envelope


@dataclass(slots=True)
class WsGateway:
    router: MessageRouter
    connection_manager: ConnectionManager
    codec: JsonMessageCodec

    def open_connection(self, connection_id: str, transport: Transport) -> None:
        self.connection_manager.open_session(connection_id, transport)

    def close_connection(self, connection_id: str) -> None:
        self.connection_manager.close_session(connection_id)

    def receive(self, connection_id: str, raw_payload: str | bytes) -> list[Envelope]:
        envelope = self.codec.decode(raw_payload)
        session = self.connection_manager.get_by_connection(connection_id)
        if session is None:
            raise ValueError(f"Unknown connection_id={connection_id}")

        if envelope.source.device_id:
            self.connection_manager.bind_device(
                connection_id,
                envelope.source.device_id,
                module=envelope.source.module,
            )

        try:
            return self.router.route(envelope)
        except Exception as exc:  # pragma: no cover - defensive path
            return [
                Envelope(
                    message_id=f"{envelope.message_id}_error",
                    trace_id=envelope.trace_id,
                    correlation_id=envelope.message_id,
                    message_type=MessageType.ERROR,
                    message_name="system.error",
                    protocol_version=envelope.protocol_version,
                    source=Endpoint(device_id=envelope.target.device_id, module="server-api"),
                    target=Endpoint(device_id=envelope.source.device_id, module=envelope.source.module),
                    timestamp=SystemClock.now_iso(),
                    payload={
                        "error_code": "router_error",
                        "error_message": str(exc),
                        "error_type": "internal_error",
                        "source": "ws_gateway",
                        "retryable": True,
                    },
                )
            ]

    def send(self, envelope: Envelope) -> None:
        session = self.connection_manager.get_by_device(envelope.target.device_id)
        if not session:
            raise ValueError(f"Target device offline: {envelope.target.device_id}")
        session.transport.send(self.codec.encode(envelope))


DEFAULT_GATEWAY_LOGGER = create_logger("server-api.gateway")
