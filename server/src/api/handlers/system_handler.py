from __future__ import annotations

import logging
from datetime import datetime, timezone

from api.session.connection_manager import ConnectionManager
from api.session.device_registry import DeviceRegistry
from infra.clock.system_clock import SystemClock
from infra.config.settings import Settings
from infra.logging import log_event
from protocol.enums import DeviceStatus, DeviceType, MessageType
from protocol.messages.envelope import Endpoint, Envelope
from protocol.messages.payloads import RegisterPayload


class SystemHandler:
    def __init__(
        self,
        *,
        settings: Settings,
        device_registry: DeviceRegistry,
        connection_manager: ConnectionManager,
        logger: logging.Logger,
    ) -> None:
        self._settings = settings
        self._device_registry = device_registry
        self._connection_manager = connection_manager
        self._logger = logger

    def handle(self, envelope: Envelope) -> list[Envelope]:
        if envelope.message_name == "system.register":
            return [self._handle_register(envelope)]
        if envelope.message_name == "system.heartbeat":
            return [self._handle_heartbeat(envelope)]
        return []

    def _handle_register(self, envelope: Envelope) -> Envelope:
        payload = RegisterPayload.from_dict(envelope.payload)
        if not payload.auth:
            raise ValueError("register.auth is required")
        if envelope.protocol_version != self._settings.protocol_version:
            raise ValueError(
                f"protocol mismatch: got={envelope.protocol_version} expected={self._settings.protocol_version}"
            )

        device = payload.device
        if device.device_type not in {DeviceType.GLASS, DeviceType.PHONE, DeviceType.SERVER}:
            raise ValueError(f"unsupported device_type={device.device_type.value}")
        if device.protocol_version != self._settings.protocol_version:
            raise ValueError(
                f"device protocol mismatch: got={device.protocol_version} expected={self._settings.protocol_version}"
            )
        if device.device_id != envelope.source.device_id:
            raise ValueError("envelope source device_id mismatch register payload")
        device.status = DeviceStatus.ONLINE
        device.last_seen_at = SystemClock.now_iso()
        self._device_registry.upsert(device)

        session = self._connection_manager.get_by_device(device.device_id)
        if not session:
            raise ValueError(f"Connection session missing for device={device.device_id}")
        session.mark_heartbeat()

        log_event(
            self._logger,
            logging.INFO,
            "system.registered",
            trace_id=envelope.trace_id,
            message_id=envelope.message_id,
            device_id=device.device_id,
            protocol_version=self._settings.protocol_version,
        )

        return Envelope(
            message_id=f"{envelope.message_id}_ack",
            trace_id=envelope.trace_id,
            correlation_id=envelope.message_id,
            message_type=MessageType.EVENT,
            message_name="system.registered",
            protocol_version=self._settings.protocol_version,
            source=Endpoint(device_id=envelope.target.device_id, module="server-api"),
            target=Endpoint(device_id=envelope.source.device_id, module=envelope.source.module),
            timestamp=SystemClock.now_iso(),
            payload={
                "result": "ok",
                "server_time": SystemClock.now_iso(),
                "protocol_version": self._settings.protocol_version,
                "heartbeat_interval_seconds": self._settings.heartbeat_interval_seconds,
            },
        )

    def _handle_heartbeat(self, envelope: Envelope) -> Envelope:
        device_id = envelope.source.device_id
        self._connection_manager.mark_heartbeat(device_id)
        self._device_registry.update_status(device_id, DeviceStatus.ONLINE, last_seen_at=SystemClock.now_iso())

        log_event(
            self._logger,
            logging.DEBUG,
            "system.heartbeat_ack",
            trace_id=envelope.trace_id,
            message_id=envelope.message_id,
            device_id=device_id,
        )

        return Envelope(
            message_id=f"{envelope.message_id}_ack",
            trace_id=envelope.trace_id,
            correlation_id=envelope.message_id,
            message_type=MessageType.ACK,
            message_name="system.heartbeat_ack",
            protocol_version=self._settings.protocol_version,
            source=Endpoint(device_id=envelope.target.device_id, module="server-api"),
            target=Endpoint(device_id=device_id, module=envelope.source.module),
            timestamp=SystemClock.now_iso(),
            payload={"ack_status": "processed"},
        )

    def reconcile_device_health(self) -> None:
        now = datetime.now(timezone.utc)
        degraded_seconds = self._settings.heartbeat_interval_seconds * 2
        offline_seconds = self._settings.heartbeat_timeout_seconds

        for device in self._device_registry.all():
            session = self._connection_manager.get_by_device(device.device_id)
            if not session:
                self._device_registry.update_status(device.device_id, DeviceStatus.OFFLINE, last_seen_at=device.last_seen_at)
                continue

            elapsed = (now - session.last_heartbeat_at).total_seconds()
            if elapsed > offline_seconds:
                self._device_registry.update_status(device.device_id, DeviceStatus.OFFLINE, last_seen_at=device.last_seen_at)
            elif elapsed > degraded_seconds:
                self._device_registry.update_status(
                    device.device_id,
                    DeviceStatus.DEGRADED,
                    last_seen_at=session.last_heartbeat_at.isoformat(),
                )
