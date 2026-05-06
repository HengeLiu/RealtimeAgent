from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from audio_chat.observability import RunRecorder
from audio_chat.protocol import (
    CONTROL_EVENTS,
    PROTOCOL_VERSION,
    SERVER_PRODUCER_ID,
    STREAM_TYPES,
    Event,
    Subscription,
    new_id,
)


class DeviceConnection(Protocol):
    device_id: str

    def push_event(self, event: Event) -> None: ...

    def push_stream_chunk(self, chunk: object) -> None: ...


@dataclass
class DeviceRecord:
    user_id: str
    device_id: str
    device_name: str
    client_type: str
    sdk_version: str
    capabilities: dict[str, Any]
    subscriptions: list[Subscription]
    connection_state: str = "online"
    connection_id: str = field(default_factory=lambda: new_id("conn"))


@dataclass(frozen=True)
class ActiveDeviceSet:
    user_id: str
    devices: tuple[DeviceRecord, ...]


@dataclass(frozen=True)
class PublishResult:
    matched_count: int
    delivered_count: int
    failed_device_ids: tuple[str, ...] = ()


class DeviceAuthenticator:
    def __init__(self, *, mode: str = "disabled", device_tokens: dict[str, str] | None = None) -> None:
        self.mode = mode
        self.device_tokens = device_tokens or {}

    def verify_token(self, registration: Event) -> tuple[bool, str | None]:
        auth = registration.payload.get("auth") or {}
        device_id = registration.payload.get("device_id")
        if self.mode == "disabled" or auth.get("mode") == "disabled":
            return True, None
        if self.mode == "static_token":
            if auth.get("mode") != "static_token":
                return False, "invalid_auth_mode"
            if self.device_tokens.get(device_id) != auth.get("token"):
                return False, "invalid_token"
            return True, None
        return False, "unsupported_auth_mode"


class RegistrationValidator:
    def validate_payload(self, event: Event) -> None:
        if event.event_name != "control.device.register.requested":
            raise ValueError("registration must use control.device.register.requested")
        device_id = event.payload.get("device_id")
        if not event.user_id or not event.producer_id or not device_id:
            raise ValueError("registration requires user_id, producer_id and payload.device_id")
        if event.producer_id != device_id:
            raise ValueError("producer_id must equal payload.device_id")
        if event.version != PROTOCOL_VERSION:
            raise ValueError("unsupported protocol version")
        self.validate_capabilities(dict(event.payload.get("capabilities") or {}))
        self.validate_subscriptions(event.payload.get("subscriptions") or [])

    def validate_capabilities(self, capabilities: dict[str, Any]) -> None:
        for key in ("streams.produce", "streams.consume"):
            for stream_type in capabilities.get(key, []) or []:
                if stream_type not in STREAM_TYPES:
                    raise ValueError(f"unknown stream_type capability: {stream_type}")

    def validate_subscriptions(self, subscriptions: list[dict[str, Any]]) -> None:
        for item in subscriptions:
            event_name = item.get("event")
            if not isinstance(event_name, str) or not event_name:
                raise ValueError("subscription.event is required")
            if event_name != "*" and not event_name.endswith("*") and event_name not in CONTROL_EVENTS:
                raise ValueError(f"unknown subscription event: {event_name}")
            if not isinstance(item.get("filter", {}), dict):
                raise ValueError("subscription.filter must be an object")


class SubscriptionMatcher:
    def match(self, event: Event, subscription: Subscription, device: DeviceRecord | None = None) -> bool:
        if not self._event_name_matches(event.event_name, subscription.event):
            return False
        for path, expected in subscription.filter.items():
            actual = self._lookup(event, path, device)
            if isinstance(actual, list):
                if expected not in actual:
                    return False
            elif actual != expected:
                return False
        return True

    @staticmethod
    def _event_name_matches(event_name: str, pattern: str) -> bool:
        if pattern == "*":
            return True
        if pattern.endswith("*"):
            return event_name.startswith(pattern[:-1])
        return event_name == pattern

    @staticmethod
    def _lookup(event: Event, path: str, device: DeviceRecord | None) -> Any:
        if path == "stream_type":
            return event.stream_type or event.payload.get("stream_type")
        if path == "producer_id":
            return event.producer_id
        if path.startswith("payload."):
            value: Any = event.payload
            for part in path.split(".")[1:]:
                value = value.get(part) if isinstance(value, dict) else None
            return value
        if path.startswith("capabilities.") and device is not None:
            return device.capabilities.get(path.removeprefix("capabilities."))
        return getattr(event, path, event.payload.get(path))


class ControlService:
    def __init__(
        self,
        *,
        authenticator: DeviceAuthenticator | None = None,
        recorder: RunRecorder | None = None,
        exclude_producer_by_default: bool = True,
    ) -> None:
        self.authenticator = authenticator or DeviceAuthenticator(mode="disabled")
        self.validator = RegistrationValidator()
        self.matcher = SubscriptionMatcher()
        self.recorder = recorder or RunRecorder()
        self.exclude_producer_by_default = exclude_producer_by_default
        self._bindings: dict[str, str] = {}
        self._devices: dict[str, DeviceRecord] = {}
        self._connections: dict[str, DeviceConnection] = {}

    def register_device(self, registration: Event, connection: DeviceConnection | None = None) -> Event:
        try:
            self.validator.validate_payload(registration)
            ok, reason = self.authenticator.verify_token(registration)
            if not ok:
                raise PermissionError(reason or "registration_denied")
            device_id = registration.payload["device_id"]
            bound_user = self._bindings.get(device_id)
            if bound_user is not None and bound_user != registration.user_id:
                raise PermissionError("device_bound_to_other_user")
            subscriptions = [
                Subscription(event=item["event"], filter=dict(item.get("filter") or {}))
                for item in registration.payload.get("subscriptions", [])
            ]
            record = DeviceRecord(
                user_id=registration.user_id,
                device_id=device_id,
                device_name=registration.payload.get("device_name", device_id),
                client_type=registration.payload.get("client_type", "unknown"),
                sdk_version=registration.payload.get("sdk_version", "unknown"),
                capabilities=dict(registration.payload.get("capabilities") or {}),
                subscriptions=subscriptions,
            )
            self._bindings[device_id] = registration.user_id
            self._devices[device_id] = record
            if connection is not None:
                self._connections[device_id] = connection
            event = Event(
                event_name="control.device.registered",
                user_id=registration.user_id,
                producer_id=SERVER_PRODUCER_ID,
                payload={
                    "device_id": device_id,
                    "connection_id": record.connection_id,
                    "heartbeat_interval_seconds": 10,
                    "effective_config": {"stream.max_chunk_bytes": 8192},
                },
            )
        except Exception as exc:
            event = Event(
                event_name="control.device.register.failed",
                user_id=registration.user_id,
                producer_id=SERVER_PRODUCER_ID,
                payload={
                    "device_id": registration.payload.get("device_id"),
                    "reason": str(exc),
                    "message": str(exc),
                },
            )
        self.recorder.record_event(event)
        return event

    def publish(self, event: Event) -> PublishResult:
        self._validate_event(event)
        self.recorder.record_event(event)
        failed: list[str] = []
        delivered = 0
        subscribers = self.resolve_subscribers(event)
        for device in subscribers:
            connection = self._connections.get(device.device_id)
            if connection is None:
                failed.append(device.device_id)
                continue
            try:
                connection.push_event(event)
                delivered += 1
            except Exception:
                failed.append(device.device_id)
                device.connection_state = "offline"
        return PublishResult(
            matched_count=len(subscribers),
            delivered_count=delivered,
            failed_device_ids=tuple(failed),
        )

    def publish_to_device(self, device_id: str, event: Event) -> PublishResult:
        self._validate_event(event)
        self.recorder.record_event(event)
        device = self._devices.get(device_id)
        if device is None or device.user_id != event.user_id or device.connection_state != "online":
            return PublishResult(matched_count=0, delivered_count=0, failed_device_ids=(device_id,))
        if not any(self.matcher.match(event, subscription, device) for subscription in device.subscriptions):
            return PublishResult(matched_count=0, delivered_count=0)
        connection = self._connections.get(device_id)
        if connection is None:
            return PublishResult(matched_count=1, delivered_count=0, failed_device_ids=(device_id,))
        try:
            connection.push_event(event)
        except Exception:
            device.connection_state = "offline"
            return PublishResult(matched_count=1, delivered_count=0, failed_device_ids=(device_id,))
        return PublishResult(matched_count=1, delivered_count=1)

    def resolve_subscribers(self, event: Event) -> list[DeviceRecord]:
        result: list[DeviceRecord] = []
        for device in self._devices.values():
            if device.user_id != event.user_id or device.connection_state != "online":
                continue
            if self.exclude_producer_by_default and device.device_id == event.producer_id:
                continue
            if any(self.matcher.match(event, subscription, device) for subscription in device.subscriptions):
                result.append(device)
        return result

    def get_active_device_set(self, user_id: str) -> ActiveDeviceSet:
        devices = tuple(
            device
            for device in self._devices.values()
            if device.user_id == user_id and device.connection_state == "online"
        )
        return ActiveDeviceSet(user_id=user_id, devices=devices)

    def append_message(self, user_id: str, message: dict[str, Any]) -> None:
        self.recorder.record_message(user_id, message)

    def build_user_snapshot(self, user_id: str) -> dict[str, Any]:
        active = self.get_active_device_set(user_id)
        return {
            "user_id": user_id,
            "devices": [
                {
                    "device_id": device.device_id,
                    "capabilities": device.capabilities,
                    "subscriptions": [subscription.__dict__ for subscription in device.subscriptions],
                    "connection_state": device.connection_state,
                }
                for device in active.devices
            ],
        }

    @staticmethod
    def _validate_event(event: Event) -> None:
        if event.event_name not in CONTROL_EVENTS:
            raise ValueError(f"unknown event_name: {event.event_name}")
        if not event.user_id or not event.producer_id:
            raise ValueError("event requires user_id and producer_id")
