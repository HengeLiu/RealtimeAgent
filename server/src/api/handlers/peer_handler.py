from __future__ import annotations

import logging

from api.session.binding_registry import BindingRegistry
from api.session.connection_manager import ConnectionManager
from infra.clock.system_clock import SystemClock
from infra.logging import log_event
from protocol.enums import MessageType
from protocol.messages.envelope import Endpoint, Envelope


class PeerHandler:
    def __init__(
        self,
        *,
        binding_registry: BindingRegistry,
        connection_manager: ConnectionManager,
        logger: logging.Logger,
    ) -> None:
        self._binding_registry = binding_registry
        self._connection_manager = connection_manager
        self._logger = logger

    def handle(self, envelope: Envelope) -> list[Envelope]:
        if envelope.message_name in {"peer.link_ready", "peer.link_established", "peer.link_broken"}:
            return [self._handle_peer_event(envelope)]

        if envelope.message_name not in {"peer.prepare_link", "peer.start_link", "peer.stop_link"}:
            return []

        glass_id = str(envelope.payload.get("glass_device_id") or "")
        phone_id = str(envelope.payload.get("phone_device_id") or "")
        if not glass_id and envelope.source.device_id.startswith("dev_glass"):
            glass_id = envelope.source.device_id
        if not phone_id:
            phone_id = self._binding_registry.get_active_phone(glass_id) or ""

        if not glass_id or not phone_id:
            return [self._error(envelope, "binding_not_found", "active binding is required")]

        active_phone = self._binding_registry.get_active_phone(glass_id)
        if active_phone != phone_id:
            return [self._error(envelope, "binding_invalid", "glass/phone binding mismatch")]

        if not self._connection_manager.get_by_device(glass_id):
            return [self._error(envelope, "device_offline", f"glass offline: {glass_id}")]
        if not self._connection_manager.get_by_device(phone_id):
            return [self._error(envelope, "device_offline", f"phone offline: {phone_id}")]

        if envelope.message_name == "peer.prepare_link":
            log_event(
                self._logger,
                logging.INFO,
                "peer.prepare_link.accepted",
                trace_id=envelope.trace_id,
                message_id=envelope.message_id,
                device_id=glass_id,
                phone_device_id=phone_id,
            )
            return [self._event(envelope, "peer.link_ready")]
        if envelope.message_name == "peer.start_link":
            log_event(
                self._logger,
                logging.INFO,
                "peer.start_link.accepted",
                trace_id=envelope.trace_id,
                message_id=envelope.message_id,
                device_id=glass_id,
                phone_device_id=phone_id,
            )
            return [self._event(envelope, "peer.link_established")]
        log_event(
            self._logger,
            logging.INFO,
            "peer.stop_link.accepted",
            trace_id=envelope.trace_id,
            message_id=envelope.message_id,
            device_id=glass_id,
            phone_device_id=phone_id,
        )
        return [self._event(envelope, "peer.link_broken")]

    def _handle_peer_event(self, envelope: Envelope) -> Envelope:
        if envelope.message_name == "peer.link_broken":
            glass_id = str(envelope.payload.get("glass_device_id") or envelope.source.device_id)
            if glass_id:
                self._binding_registry.break_binding(glass_id)

        log_event(
            self._logger,
            logging.INFO,
            envelope.message_name,
            trace_id=envelope.trace_id,
            message_id=envelope.message_id,
            device_id=envelope.source.device_id,
            link_id=str(envelope.payload.get("link_id", "")),
        )
        return Envelope(
            message_id=f"{envelope.message_id}_ack",
            trace_id=envelope.trace_id,
            correlation_id=envelope.message_id,
            message_type=MessageType.ACK,
            message_name="peer.ack",
            protocol_version=envelope.protocol_version,
            source=Endpoint(device_id=envelope.target.device_id, module="server-api"),
            target=Endpoint(device_id=envelope.source.device_id, module=envelope.source.module),
            timestamp=SystemClock.now_iso(),
            payload={"ack_status": "processed"},
        )

    def _event(self, envelope: Envelope, event_name: str) -> Envelope:
        payload = dict(envelope.payload)
        payload.setdefault("link_id", payload.get("link_id", "link_pending"))
        return Envelope(
            message_id=f"{envelope.message_id}_{event_name.split('.')[-1]}",
            trace_id=envelope.trace_id,
            correlation_id=envelope.message_id,
            message_type=MessageType.EVENT,
            message_name=event_name,
            protocol_version=envelope.protocol_version,
            source=Endpoint(device_id=envelope.target.device_id, module="server-api"),
            target=Endpoint(device_id=envelope.source.device_id, module=envelope.source.module),
            timestamp=SystemClock.now_iso(),
            payload=payload,
        )

    def _error(self, envelope: Envelope, code: str, message: str) -> Envelope:
        return Envelope(
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
                "error_code": code,
                "error_message": message,
                "error_type": "validation_error",
                "source": "peer_handler",
                "retryable": True,
            },
        )
