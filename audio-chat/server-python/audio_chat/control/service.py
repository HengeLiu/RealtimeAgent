from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
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
    validate_control_event_payload,
    validate_event_name,
)


class DeviceConnection(Protocol):
    device_id: str

    def push_event(self, event: Event) -> None: ...

    def push_stream_chunk(self, chunk: object) -> None: ...

    def close(self, *, reason: str) -> None: ...


@dataclass
class Device:
    """Control Service 内部运行态设备对象。"""

    user_id: str
    device_id: str
    device_name: str
    client_type: str
    sdk_version: str
    capabilities: dict[str, Any]
    subscriptions: list[Subscription]
    connection_state: str = "online"
    connection_id: str = field(default_factory=lambda: new_id("conn"))
    last_seen_at: float = field(default_factory=time.time)
    last_error: dict[str, Any] | None = None
    register_failed_reason: str | None = None
    auth_diagnostics: dict[str, Any] = field(default_factory=dict)
    binding_diagnostics: dict[str, Any] = field(default_factory=dict)


DeviceRecord = Device


@dataclass(frozen=True)
class DeviceSnapshot:
    """只读设备快照。"""

    user_id: str
    device_id: str
    device_name: str
    client_type: str
    sdk_version: str
    connection_id: str
    last_seen_at: float
    connection_state: str
    capabilities: dict[str, Any]
    subscriptions: tuple[Subscription, ...]
    last_error: dict[str, Any] | None = None
    register_failed_reason: str | None = None
    auth_diagnostics: dict[str, Any] = field(default_factory=dict)
    binding_diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为 debug API JSON 字典。"""

        return {
            "user_id": self.user_id,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "client_type": self.client_type,
            "sdk_version": self.sdk_version,
            "connection_id": self.connection_id,
            "last_seen_at": self.last_seen_at,
            "connection_state": self.connection_state,
            "capabilities": dict(self.capabilities),
            "subscriptions": [subscription.__dict__ for subscription in self.subscriptions],
            "last_error": self.last_error,
            "register_failed_reason": self.register_failed_reason,
            "auth": dict(self.auth_diagnostics),
            "binding": dict(self.binding_diagnostics),
        }


@dataclass(frozen=True)
class ActiveDeviceSet:
    user_id: str
    devices: tuple[Device, ...]


@dataclass(frozen=True)
class PublishResult:
    matched_count: int
    delivered_count: int
    failed_device_ids: tuple[str, ...] = ()
    matched_device_ids: tuple[str, ...] = ()


class PairingTokenIssuer(Protocol):
    """配对 token 签发接口。

    主要功能：为后续管理端或配对服务预留正式 token 签发边界。
    主要方法：`issue_token()` 接收用户、设备、过期时间和 nonce，返回端侧注册使用的 token。
    """

    def issue_token(self, *, user_id: str, device_id: str, expires_at: int, nonce: str) -> str: ...


class HmacSignedTokenIssuer:
    """基于 HMAC-SHA256 的 signed_token 签发器。

    主要功能：给单元测试、本地配对服务和后续管理端提供一致 token 格式。
    主要属性：`secret` 是签名密钥，必须由调用方从安全配置或环境变量传入。
    """

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("signed token secret is required")
        self.secret = secret

    def issue_token(self, *, user_id: str, device_id: str, expires_at: int, nonce: str) -> str:
        """签发设备注册 token。

        主要逻辑：把 `user_id`、`device_id`、`expires_at`、`nonce` 组成规范 JSON，
        用 HMAC-SHA256 签名后编码成 `payload.signature`。
        参数：用户编号、设备编号、过期 Unix 秒和随机 nonce。
        返回值：URL 安全的 token 字符串。
        异常情况：参数为空时抛出 `ValueError`。
        """

        if not user_id or not device_id or not nonce:
            raise ValueError("user_id, device_id and nonce are required")
        payload = {
            "user_id": user_id,
            "device_id": device_id,
            "expires_at": int(expires_at),
            "nonce": nonce,
        }
        payload_raw = _canonical_json(payload).encode("utf-8")
        signature = hmac.new(self.secret.encode("utf-8"), payload_raw, hashlib.sha256).digest()
        return f"{_b64url_encode(payload_raw)}.{_b64url_encode(signature)}"


def _canonical_json(data: dict[str, Any]) -> str:
    """生成签名使用的规范 JSON 字符串。"""

    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _b64url_encode(raw: bytes) -> str:
    """生成不带等号填充的 URL 安全 base64。"""

    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    """解码不带等号填充的 URL 安全 base64。"""

    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(f"{text}{padding}".encode("ascii"))


class DeviceAuthenticator:
    """设备注册鉴权器。

    主要功能：校验 disabled、static_token 和 signed_token 三种注册模式。
    主要属性：`signed_token_secret_env` 指向签名密钥环境变量，`token_clock_skew_seconds`
    控制正式 token 的时钟偏差容忍。
    """

    def __init__(
        self,
        *,
        mode: str = "disabled",
        device_tokens: dict[str, str] | None = None,
        signed_token_secret_env: str = "AUDIO_CHAT_DEVICE_TOKEN_SECRET",
        token_clock_skew_seconds: int = 60,
        now: Any | None = None,
    ) -> None:
        self.mode = mode
        self.device_tokens = device_tokens or {}
        self.signed_token_secret_env = signed_token_secret_env
        self.token_clock_skew_seconds = int(token_clock_skew_seconds)
        self._now = now or time.time

    def verify_token(self, registration: Event) -> tuple[bool, str | None]:
        auth = registration.payload.get("auth") or {}
        device_id = registration.payload.get("device_id")
        if self.mode == "disabled":
            return True, None
        if self.mode == "static_token":
            if auth.get("mode") != "static_token":
                return False, "invalid_auth_mode"
            if self.device_tokens.get(device_id) != auth.get("token"):
                return False, "invalid_token"
            return True, None
        if self.mode == "signed_token":
            return self._verify_signed_token(registration, auth)
        return False, "unsupported_auth_mode"

    def _verify_signed_token(self, registration: Event, auth: dict[str, Any]) -> tuple[bool, str | None]:
        """校验正式 signed_token。

        主要逻辑：从环境变量读取密钥，解析 `payload.signature`，校验签名、过期时间、
        user_id 和 device_id 是否与注册事件一致。
        参数：注册事件和 payload.auth。
        返回值：`(True, None)` 或 `(False, reason)`。
        异常情况：解析错误会转换为明确 reason，不向外抛出底层异常。
        """

        if auth.get("mode") != "signed_token":
            return False, "invalid_auth_mode"
        secret = os.environ.get(self.signed_token_secret_env, "")
        if not secret:
            return False, "signed_token_secret_missing"
        token = str(auth.get("token") or "")
        if not token or token.count(".") != 1:
            return False, "malformed_signed_token"
        payload_part, signature_part = token.split(".", 1)
        try:
            payload_raw = _b64url_decode(payload_part)
            payload = json.loads(payload_raw.decode("utf-8"))
            signature = _b64url_decode(signature_part)
        except Exception:
            return False, "malformed_signed_token"
        expected = hmac.new(secret.encode("utf-8"), payload_raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            return False, "invalid_signed_token_signature"
        token_user_id = str(payload.get("user_id") or "")
        token_device_id = str(payload.get("device_id") or "")
        if token_user_id != registration.user_id:
            return False, "signed_token_user_mismatch"
        if token_device_id != registration.payload.get("device_id"):
            return False, "signed_token_device_mismatch"
        try:
            expires_at = int(payload.get("expires_at"))
        except Exception:
            return False, "malformed_signed_token"
        if expires_at + self.token_clock_skew_seconds < int(self._now()):
            return False, "signed_token_expired"
        nonce = str(payload.get("nonce") or "")
        if not nonce:
            return False, "malformed_signed_token"
        return True, None


class RegistrationValidator:
    def __init__(
        self,
        *,
        max_subscriptions_per_device: int = 64,
        allow_subscribe_all: bool = False,
        subscription_filter_mode: str = "exact",
    ) -> None:
        self.max_subscriptions_per_device = max_subscriptions_per_device
        self.allow_subscribe_all = allow_subscribe_all
        self.subscription_filter_mode = subscription_filter_mode

    def validate_payload(self, event: Event) -> None:
        validate_event_name(event.event_name)
        validate_control_event_payload(event.payload)
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
        if len(subscriptions) > self.max_subscriptions_per_device:
            raise ValueError("too many subscriptions for device")
        for item in subscriptions:
            event_name = item.get("event")
            if not isinstance(event_name, str) or not event_name:
                raise ValueError("subscription.event is required")
            if event_name == "*" and not self.allow_subscribe_all:
                raise ValueError("subscription '*' is disabled by config")
            if event_name != "*" and not event_name.endswith("*") and event_name not in CONTROL_EVENTS:
                raise ValueError(f"unknown subscription event: {event_name}")
            if not isinstance(item.get("filter", {}), dict):
                raise ValueError("subscription.filter must be an object")
            self.validate_filter(dict(item.get("filter") or {}))
            if self.subscription_filter_mode != "exact":
                raise ValueError(f"unsupported subscription_filter_mode: {self.subscription_filter_mode}")

    def validate_filter(self, filter_data: dict[str, Any]) -> None:
        """校验订阅 filter 只使用协议支持的简单精确匹配。"""

        allowed_envelope_fields = {
            "version",
            "event_id",
            "event_name",
            "timestamp_ms",
            "user_id",
            "producer_id",
            "session_id",
            "stream_id",
            "stream_type",
        }
        for path, expected in filter_data.items():
            if not isinstance(path, str) or not path:
                raise ValueError("subscription.filter path must be a non-empty string")
            if any(token in path for token in ("$", "[", "]", "(", ")", "|", "^")):
                raise ValueError(f"unsupported subscription filter expression: {path}")
            if not (path.startswith(("payload.", "capabilities.")) or path in allowed_envelope_fields):
                raise ValueError(f"unsupported subscription filter path: {path}")
            if isinstance(expected, dict):
                raise ValueError(f"subscription filter value must be scalar or list: {path}")


class SubscriptionMatcher:
    def match(self, event: Event, subscription: Subscription, device: Device | None = None) -> bool:
        if not self._event_name_matches(event.event_name, subscription.event):
            return False
        for path, expected in subscription.filter.items():
            actual = self._lookup(event, path, device)
            if isinstance(actual, list):
                if isinstance(expected, list):
                    if not all(item in actual for item in expected):
                        return False
                elif expected not in actual:
                    return False
            elif isinstance(expected, list):
                if actual not in expected:
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
    def _lookup(event: Event, path: str, device: Device | None) -> Any:
        if path.startswith("payload."):
            value: Any = event.payload
            for part in path.split(".")[1:]:
                value = value.get(part) if isinstance(value, dict) else None
            return value
        if path.startswith("capabilities.") and device is not None:
            return device.capabilities.get(path.removeprefix("capabilities."))
        return getattr(event, path, None)


class ControlService:
    def __init__(
        self,
        *,
        authenticator: DeviceAuthenticator | None = None,
        recorder: RunRecorder | None = None,
        exclude_producer_by_default: bool = True,
        max_subscriptions_per_device: int = 64,
        allow_subscribe_all: bool = False,
        subscription_filter_mode: str = "exact",
        active_device_set_policy: str = "single",
        effective_config: dict[str, Any] | None = None,
    ) -> None:
        self.authenticator = authenticator or DeviceAuthenticator(mode="disabled")
        self.validator = RegistrationValidator(
            max_subscriptions_per_device=max_subscriptions_per_device,
            allow_subscribe_all=allow_subscribe_all,
            subscription_filter_mode=subscription_filter_mode,
        )
        self.matcher = SubscriptionMatcher()
        self.recorder = recorder or RunRecorder()
        self.exclude_producer_by_default = exclude_producer_by_default
        self.active_device_set_policy = active_device_set_policy
        self.effective_config = dict(effective_config or {})
        self._bindings: dict[str, str] = {}
        self._devices: dict[str, Device] = {}
        self._connections: dict[str, DeviceConnection] = {}
        self._registration_failures: dict[str, DeviceSnapshot] = {}

    def register_device(self, registration: Event, connection: DeviceConnection | None = None) -> Event:
        auth_mode = str((registration.payload.get("auth") or {}).get("mode") or self.authenticator.mode)
        failed_device_id = str(registration.payload.get("device_id") or registration.producer_id or "unknown")
        try:
            if self.active_device_set_policy != "single":
                raise ValueError(f"unsupported active_device_set_policy: {self.active_device_set_policy}")
            self.validator.validate_payload(registration)
            ok, reason = self.authenticator.verify_token(registration)
            if not ok:
                raise PermissionError(reason or "registration_denied")
            device_id = registration.payload["device_id"]
            bound_user = self._bindings.get(device_id)
            if bound_user is not None and bound_user != registration.user_id:
                raise PermissionError("device_bound_to_other_user")
            old_connection = self._connections.get(device_id)
            subscriptions = [
                Subscription(event=item["event"], filter=dict(item.get("filter") or {}))
                for item in registration.payload.get("subscriptions", [])
            ]
            record = Device(
                user_id=registration.user_id,
                device_id=device_id,
                device_name=registration.payload.get("device_name", device_id),
                client_type=registration.payload.get("client_type", "unknown"),
                sdk_version=registration.payload.get("sdk_version", "unknown"),
                capabilities=dict(registration.payload.get("capabilities") or {}),
                subscriptions=subscriptions,
                auth_diagnostics={
                    "mode": self.authenticator.mode,
                    "request_mode": auth_mode,
                    "status": "passed",
                    "token_present": bool((registration.payload.get("auth") or {}).get("token")),
                    "signed_token_secret_env": (
                        self.authenticator.signed_token_secret_env
                        if self.authenticator.mode == "signed_token"
                        else None
                    ),
                },
                binding_diagnostics={
                    "policy": self.active_device_set_policy,
                    "bound_user_id": registration.user_id,
                    "replaced_connection": old_connection is not None and old_connection is not connection,
                    "conflict_user_id": None,
                },
            )
            self._bindings[device_id] = registration.user_id
            if old_connection is not None and old_connection is not connection:
                try:
                    old_connection.close(reason="replaced_by_new_connection")
                except Exception:
                    pass
                self.recorder.record_event(
                    Event(
                        event_name="control.device.state.changed",
                        user_id=registration.user_id,
                        producer_id=SERVER_PRODUCER_ID,
                        payload={
                            "device_id": device_id,
                            "connection_state": "replaced",
                            "reason": "replaced_by_new_connection",
                        },
                    )
                )
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
                    "effective_config": dict(self.effective_config),
                },
            )
        except Exception as exc:
            reason = str(exc)
            previous_user = self._bindings.get(failed_device_id)
            snapshot = DeviceSnapshot(
                user_id=registration.user_id,
                device_id=failed_device_id,
                device_name=str(registration.payload.get("device_name") or failed_device_id),
                client_type=str(registration.payload.get("client_type") or "unknown"),
                sdk_version=str(registration.payload.get("sdk_version") or "unknown"),
                connection_id="",
                last_seen_at=time.time(),
                connection_state="offline",
                capabilities=dict(registration.payload.get("capabilities") or {}),
                subscriptions=tuple(
                    Subscription(event=str(item.get("event", "")), filter=dict(item.get("filter") or {}))
                    for item in registration.payload.get("subscriptions", [])
                    if isinstance(item, dict)
                ),
                last_error={"code": "registration_failed", "message": reason},
                register_failed_reason=reason,
                auth_diagnostics={
                    "mode": self.authenticator.mode,
                    "request_mode": auth_mode,
                    "status": "failed",
                    "reason": reason,
                    "token_present": bool((registration.payload.get("auth") or {}).get("token")),
                    "signed_token_secret_env": (
                        self.authenticator.signed_token_secret_env
                        if self.authenticator.mode == "signed_token"
                        else None
                    ),
                },
                binding_diagnostics={
                    "policy": self.active_device_set_policy,
                    "bound_user_id": previous_user,
                    "conflict_user_id": registration.user_id if previous_user not in {None, registration.user_id} else None,
                },
            )
            if previous_user is None or previous_user == registration.user_id:
                self._registration_failures[failed_device_id] = snapshot
            else:
                self._registration_failures[f"{failed_device_id}:{registration.user_id}"] = snapshot
                existing = self._devices.get(failed_device_id)
                if existing is not None:
                    existing.binding_diagnostics["last_conflict_user_id"] = registration.user_id
                    existing.binding_diagnostics["last_conflict_reason"] = reason
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
        return self._deliver_to_devices(event, subscribers)

    def publish_matching(
        self,
        event: Event,
        *,
        require_capability: str | None = None,
        selection: str = "all",
    ) -> PublishResult:
        """按协议订阅、能力和选择策略发布事件。

        主要逻辑：先按当前事件订阅找到候选设备，再按 capability 过滤，最后按 selection
        选择全部或第一台可用设备。调用方不能传入 device_id，因此不会形成业务层点对点
        发送事件接口。
        参数：`event` 为协议事件，`require_capability` 为可选能力条件，`selection` 为
        `all` 或 `first_available`。
        返回值：投递统计。
        异常情况：事件名或 selection 非法时抛出 `ValueError`。
        """
        self._validate_event(event)
        self.recorder.record_event(event)
        selected = self.resolve_matching_devices(
            event,
            require_capability=require_capability,
            selection=selection,
        )
        return self._deliver_to_devices(event, selected)

    def resolve_matching_devices(
        self,
        event: Event,
        *,
        require_capability: str | None = None,
        selection: str = "all",
    ) -> list[Device]:
        """按订阅、能力和选择策略解析目标设备。

        主要逻辑：先使用协议订阅匹配候选设备，再按 capability 过滤，最后应用
        `all` 或 `first_available`。该方法不投递事件，供 Stream Service 在创建
        output stream 前冻结 consumer 列表。
        参数：`event` 为协议事件，`require_capability` 为可选能力，`selection` 为
        选择策略。
        返回值：最终匹配的设备记录列表。
        异常情况：事件名或 selection 非法时抛出 `ValueError`。
        """
        self._validate_event(event)
        candidates = self.resolve_subscribers(event)
        if require_capability is not None:
            candidates = [device for device in candidates if self._has_capability(device.capabilities, require_capability)]
        if selection == "all":
            return candidates
        if selection == "first_available":
            return candidates[:1]
        raise ValueError(f"unsupported device selection: {selection}")

    def _deliver_to_devices(self, event: Event, devices: list[Device]) -> PublishResult:
        failed: list[str] = []
        delivered = 0
        for device in devices:
            connection = self._connections.get(device.device_id)
            if connection is None:
                failed.append(device.device_id)
                continue
            try:
                connection.push_event(event)
                delivered += 1
            except Exception as exc:
                failed.append(device.device_id)
                device.connection_state = "offline"
                self._record_device_error(device, "event_delivery_failed", str(exc))
        return PublishResult(
            matched_count=len(devices),
            delivered_count=delivered,
            failed_device_ids=tuple(failed),
            matched_device_ids=tuple(device.device_id for device in devices),
        )

    def _push_event_to_device_ids(self, event: Event, device_ids: tuple[str, ...]) -> PublishResult:
        """向已冻结的内部设备集合推送事件。

        主要逻辑：仅供 Stream Service 关闭或取消已打开的 output stream 时使用，
        目标列表来自打开 stream 时的协议匹配结果，不向 Tool / Task 暴露 device_id
        点对点发送接口。
        参数：`event` 为协议事件，`device_ids` 为内部冻结 consumer 列表。
        返回值：投递统计。
        异常情况：事件非法时抛出 `ValueError`。
        """
        self._validate_event(event)
        self.recorder.record_event(event)
        devices = [self._devices[device_id] for device_id in device_ids if device_id in self._devices]
        return self._deliver_to_devices(event, devices)

    def push_stream_chunk_to_devices(self, device_ids: tuple[str, ...], chunk: object) -> PublishResult:
        failed: list[str] = []
        delivered = 0
        for device_id in device_ids:
            device = self._devices.get(device_id)
            connection = self._connections.get(device_id)
            if device is None or connection is None or device.connection_state != "online":
                failed.append(device_id)
                continue
            try:
                connection.push_stream_chunk(chunk)
                delivered += 1
            except Exception as exc:
                device.connection_state = "offline"
                self._record_device_error(device, "stream_delivery_failed", str(exc))
                failed.append(device_id)
        return PublishResult(
            matched_count=len(device_ids),
            delivered_count=delivered,
            failed_device_ids=tuple(failed),
            matched_device_ids=device_ids,
        )

    def record_heartbeat(self, event: Event) -> Event:
        self._validate_event(event)
        device = self._devices.get(event.producer_id)
        if device is not None and device.user_id == event.user_id:
            device.last_seen_at = time.time()
            device.connection_state = "online"
        self.recorder.record_event(event)
        return event

    def mark_connection_offline(self, device_id: str, *, connection_id: str | None = None, reason: str = "disconnected") -> None:
        device = self._devices.get(device_id)
        if device is None:
            return
        if connection_id is not None and device.connection_id != connection_id:
            return
        device.connection_state = "offline"
        device.last_seen_at = time.time()
        self._record_device_error(device, "connection_offline", reason)
        self._connections.pop(device_id, None)
        self.recorder.record_event(
            Event(
                event_name="control.device.state.changed",
                user_id=device.user_id,
                producer_id=SERVER_PRODUCER_ID,
                payload={
                    "device_id": device.device_id,
                    "connection_id": device.connection_id,
                    "connection_state": "offline",
                    "reason": reason,
                },
            )
        )

    def expire_stale_devices(self, *, now: float | None = None, timeout_seconds: float = 30.0) -> tuple[str, ...]:
        """按心跳超时标记离线设备。"""

        current = time.time() if now is None else now
        expired: list[str] = []
        for device in self._devices.values():
            if device.connection_state != "online":
                continue
            if current - device.last_seen_at <= timeout_seconds:
                continue
            device.connection_state = "offline"
            self._connections.pop(device.device_id, None)
            self._record_device_error(device, "heartbeat_timeout", "heartbeat timeout")
            expired.append(device.device_id)
            self.recorder.record_event(
                Event(
                    event_name="control.device.state.changed",
                    user_id=device.user_id,
                    producer_id=SERVER_PRODUCER_ID,
                    payload={
                        "device_id": device.device_id,
                        "connection_id": device.connection_id,
                        "connection_state": "offline",
                        "reason": "heartbeat_timeout",
                    },
                )
            )
        return tuple(expired)

    def resolve_subscribers(self, event: Event) -> list[Device]:
        result: list[Device] = []
        for device in self._devices.values():
            if device.user_id != event.user_id or device.connection_state != "online":
                continue
            if self.exclude_producer_by_default and device.device_id == event.producer_id:
                continue
            if any(self.matcher.match(event, subscription, device) for subscription in device.subscriptions):
                result.append(device)
        return result

    @staticmethod
    def _has_capability(capabilities: dict[str, Any], capability: str) -> bool:
        if capabilities.get(capability):
            return True
        return capability in capabilities.get("streams.produce", []) or capability in capabilities.get("streams.consume", [])

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
        return {
            "user_id": user_id,
            "devices": [
                self._device_snapshot(device).to_dict()
                for device in self._devices.values()
                if device.user_id == user_id
            ],
            "registration_failures": [
                snapshot.to_dict()
                for snapshot in self._registration_failures.values()
                if snapshot.user_id == user_id
            ],
        }

    def build_devices_snapshot(self) -> dict[str, Any]:
        return {"devices": [self._device_snapshot(device).to_dict() for device in self._devices.values()]}

    def build_device_snapshot(self, device_id: str) -> dict[str, Any] | None:
        """返回单设备 debug 快照。"""

        device = self._devices.get(device_id)
        if device is not None:
            return self._device_snapshot(device).to_dict()
        failure = self._registration_failures.get(device_id)
        return failure.to_dict() if failure is not None else None

    @staticmethod
    def _device_snapshot(device: Device) -> DeviceSnapshot:
        return DeviceSnapshot(
            user_id=device.user_id,
            device_id=device.device_id,
            device_name=device.device_name,
            client_type=device.client_type,
            sdk_version=device.sdk_version,
            connection_id=device.connection_id,
            last_seen_at=device.last_seen_at,
            connection_state=device.connection_state,
            capabilities=dict(device.capabilities),
            subscriptions=tuple(device.subscriptions),
            last_error=dict(device.last_error) if device.last_error is not None else None,
            register_failed_reason=device.register_failed_reason,
            auth_diagnostics=dict(device.auth_diagnostics),
            binding_diagnostics=dict(device.binding_diagnostics),
        )

    @staticmethod
    def _validate_event(event: Event) -> None:
        validate_event_name(event.event_name)
        validate_control_event_payload(event.payload)
        if event.event_name not in CONTROL_EVENTS:
            raise ValueError(f"unknown event_name: {event.event_name}")
        if not event.user_id or not event.producer_id:
            raise ValueError("event requires user_id and producer_id")
        if any(key in event.payload for key in ("target_device", "target_device_id", "source_device", "source_device_id")):
            raise ValueError("event payload must not contain target/source device fields")

    @staticmethod
    def _record_device_error(device: Device, code: str, message: str) -> None:
        device.last_error = {"code": code, "message": message, "timestamp_ms": int(time.time() * 1000)}
