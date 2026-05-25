from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from realtime_agent.conversation import ConversationMemoryService, MessageSummary
from realtime_agent.device_capabilities import (
    compile_internal_routes_from_supports,
    compile_registration_payload,
    compile_system_routes_from_properties,
)
from realtime_agent.observability import RunRecorder
from realtime_agent.protocol import (
    CONTROL_EVENTS,
    PROTOCOL_VERSION,
    SERVER_PRODUCER_ID,
    Event,
    is_allowed_event_name,
    is_allowed_route_event,
    new_id,
    validate_control_event_payload,
    validate_event_name,
)


class DeviceConnection(Protocol):
    device_id: str

    def push_event(self, event: Event) -> None: ...

    def push_stream_chunk(self, chunk: object) -> None: ...

    def close(self, *, reason: str) -> None: ...


@dataclass(frozen=True)
class _Route:
    """Control Service 内部事件路由规则。"""

    event: str
    filter: dict[str, Any] = field(default_factory=dict)


@dataclass
class Device:
    """Control Service 内部运行态设备对象。"""

    user_id: str
    device_id: str
    name: str
    device_name: str
    client_type: str
    sdk_version: str
    properties: dict[str, Any]
    routes: list[_Route]
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
    """只读设备快照。

    主要功能：给 debug API 暴露设备身份、连接状态和 properties。
    不包含 capabilities 字段，避免把设备路由理解成第二套能力系统。
    """

    user_id: str
    device_id: str
    name: str
    device_name: str
    client_type: str
    sdk_version: str
    properties: dict[str, Any]
    connection_id: str
    last_seen_at: float
    connection_state: str
    routes: tuple[_Route, ...]
    last_error: dict[str, Any] | None = None
    register_failed_reason: str | None = None
    auth_diagnostics: dict[str, Any] = field(default_factory=dict)
    binding_diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为 debug API JSON 字典。"""

        return {
            "user_id": self.user_id,
            "device_id": self.device_id,
            "name": self.name,
            "device_name": self.device_name,
            "client_type": self.client_type,
            "sdk_version": self.sdk_version,
            "properties": dict(self.properties),
            "connection_id": self.connection_id,
            "last_seen_at": self.last_seen_at,
            "connection_state": self.connection_state,
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
    route_diagnostics: tuple[dict[str, Any], ...] = ()


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
        signed_token_secret_env: str = "REALTIME_AGENT_DEVICE_TOKEN_SECRET",
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
        max_routes_per_device: int = 64,
        allow_route_all: bool = False,
        route_filter_mode: str = "exact",
    ) -> None:
        self.max_routes_per_device = max_routes_per_device
        self.allow_route_all = allow_route_all
        self.route_filter_mode = route_filter_mode

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
        if "capabilities" in event.payload:
            raise ValueError("registration payload must not contain capabilities; use structured supports")

    def validate_routes(self, routes: list[dict[str, Any]]) -> None:
        if len(routes) > self.max_routes_per_device:
            raise ValueError("too many routes for device")
        for item in routes:
            event_name = item.get("event")
            if not isinstance(event_name, str) or not event_name:
                raise ValueError("route.event is required")
            if event_name == "*" and not self.allow_route_all:
                raise ValueError("route '*' is disabled by config")
            if event_name != "*" and not is_allowed_route_event(event_name):
                raise ValueError(f"unknown route event: {event_name}")
            if not isinstance(item.get("filter", {}), dict):
                raise ValueError("route.filter must be an object")
            self.validate_filter(dict(item.get("filter") or {}))
            if self.route_filter_mode != "exact":
                raise ValueError(f"unsupported route_filter_mode: {self.route_filter_mode}")

    def validate_filter(self, filter_data: dict[str, Any]) -> None:
        """校验路由 filter 只使用协议支持的简单精确匹配。"""

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
                raise ValueError("route.filter path must be a non-empty string")
            if any(token in path for token in ("$", "[", "]", "(", ")", "|", "^")):
                raise ValueError(f"unsupported route filter expression: {path}")
            if not (path.startswith("payload.") or path in allowed_envelope_fields):
                raise ValueError(f"unsupported route filter path: {path}")
            if isinstance(expected, dict):
                raise ValueError(f"route filter value must be scalar or list: {path}")


class RouteMatcher:
    def match(self, event: Event, route: _Route, device: Device | None = None) -> bool:
        """判断单条路由是否命中事件。"""

        return bool(self.explain(event, route, device)["matched"])

    def explain(self, event: Event, route: _Route, device: Device | None = None) -> dict[str, Any]:
        """返回单条路由的匹配诊断。

        主要逻辑：先匹配事件名，再逐个检查 filter 字段。返回结构只包含小型结构化
        诊断，不包含媒体 payload。
        参数：`event` 为待分发事件，`route` 为内部路由规则，`device` 为可选设备状态。
        返回值：包含 `matched/reason/route/filter` 的字典。
        异常情况：无。
        """

        if not self._event_name_matches(event.event_name, route.event):
            return {
                "matched": False,
                "reason": "event_name_mismatch",
                "route": route.event,
                "filter": dict(route.filter),
            }
        for path, expected in route.filter.items():
            actual = self._lookup(event, path, device)
            if isinstance(actual, list):
                if isinstance(expected, list):
                    if not all(item in actual for item in expected):
                        return self._filter_miss(route, path, expected, actual)
                elif expected not in actual:
                    return self._filter_miss(route, path, expected, actual)
            elif isinstance(expected, list):
                if actual not in expected:
                    return self._filter_miss(route, path, expected, actual)
            elif actual != expected:
                return self._filter_miss(route, path, expected, actual)
        return {
            "matched": True,
            "reason": "matched",
            "route": route.event,
            "filter": dict(route.filter),
        }

    @staticmethod
    def _filter_miss(route: _Route, path: str, expected: Any, actual: Any) -> dict[str, Any]:
        """生成 filter 未命中的可读诊断。"""

        return {
            "matched": False,
            "reason": "filter_mismatch",
            "route": route.event,
            "filter": dict(route.filter),
            "path": path,
            "expected": expected,
            "actual": actual,
        }

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
        return getattr(event, path, None)


class ControlService:
    def __init__(
        self,
        *,
        authenticator: DeviceAuthenticator | None = None,
        recorder: RunRecorder | None = None,
        exclude_producer_by_default: bool = True,
        max_routes_per_device: int = 64,
        allow_route_all: bool = False,
        route_filter_mode: str = "exact",
        active_device_set_policy: str = "single",
        effective_config: dict[str, Any] | None = None,
        conversation_memory: ConversationMemoryService | None = None,
    ) -> None:
        self.authenticator = authenticator or DeviceAuthenticator(mode="disabled")
        self.validator = RegistrationValidator(
            max_routes_per_device=max_routes_per_device,
            allow_route_all=allow_route_all,
            route_filter_mode=route_filter_mode,
        )
        self.matcher = RouteMatcher()
        self.recorder = recorder or RunRecorder()
        self.exclude_producer_by_default = exclude_producer_by_default
        self.active_device_set_policy = active_device_set_policy
        self.effective_config = dict(effective_config or {})
        self.conversation_memory = conversation_memory
        self._bindings: dict[str, str] = {}
        self._devices: dict[str, Device] = {}
        self._connections: dict[str, DeviceConnection] = {}
        self._registration_failures: dict[str, DeviceSnapshot] = {}

    def register_device(self, registration: Event, connection: DeviceConnection | None = None) -> Event:
        auth_mode = str((registration.payload.get("auth") or {}).get("mode") or self.authenticator.mode)
        failed_device_id = str(registration.payload.get("device_id") or registration.producer_id or "unknown")
        try:
            compiled_payload = compile_registration_payload(registration.payload)
            registration = Event(
                event_name=registration.event_name,
                user_id=registration.user_id,
                producer_id=registration.producer_id,
                payload=compiled_payload,
                version=registration.version,
                event_id=registration.event_id,
                timestamp_ms=registration.timestamp_ms,
                session_id=registration.session_id,
                stream_id=registration.stream_id,
                stream_type=registration.stream_type,
            )
            if self.active_device_set_policy != "single":
                raise ValueError(f"unsupported active_device_set_policy: {self.active_device_set_policy}")
            routes = [
                _Route(event=item["event"], filter=dict(item.get("filter") or {}))
                for item in compile_internal_routes_from_supports(compiled_payload["supports"])
            ]
            routes.extend(
                _Route(event=item["event"], filter=dict(item.get("filter") or {}))
                for item in compile_system_routes_from_properties(compiled_payload.get("properties"))
            )
            self.validator.validate_payload(registration)
            self.validator.validate_routes([{"event": route.event, "filter": dict(route.filter)} for route in routes])
            ok, reason = self.authenticator.verify_token(registration)
            if not ok:
                raise PermissionError(reason or "registration_denied")
            device_id = registration.payload["device_id"]
            bound_user = self._bindings.get(device_id)
            if bound_user is not None and bound_user != registration.user_id:
                raise PermissionError("device_bound_to_other_user")
            old_connection = self._connections.get(device_id)
            name = str(registration.payload.get("name") or registration.payload.get("device_name") or device_id)
            properties = dict(registration.payload.get("properties") or {})
            record = Device(
                user_id=registration.user_id,
                device_id=device_id,
                name=name,
                device_name=str(registration.payload.get("device_name") or name),
                client_type=registration.payload.get("client_type", "unknown"),
                sdk_version=registration.payload.get("sdk_version", "unknown"),
                properties=properties,
                routes=routes,
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
                name=str(registration.payload.get("name") or registration.payload.get("device_name") or failed_device_id),
                device_name=str(
                    registration.payload.get("device_name")
                    or registration.payload.get("name")
                    or failed_device_id
                ),
                client_type=str(registration.payload.get("client_type") or "unknown"),
                sdk_version=str(registration.payload.get("sdk_version") or "unknown"),
                properties=dict(registration.payload.get("properties") or {}),
                connection_id="",
                last_seen_at=time.time(),
                connection_state="offline",
                routes=tuple(),
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
        recipients, route = self._resolve_recipients_with_diagnostics(event)
        result = self._deliver_to_devices(event, recipients, route_diagnostics=route)
        self._record_route(event, result)
        return result

    def publish_matching(
        self,
        event: Event,
        *,
        selection: str = "all",
    ) -> PublishResult:
        """按协议订阅和选择策略发布事件。

        主要逻辑：先按当前事件订阅找到候选设备，再按 selection 选择全部或第一台
        可用设备。调用方不能传入 device_id，因此不会形成业务层点对点发送事件接口。
        参数：`event` 为协议事件，`selection` 为 `all` 或 `first_available`。
        返回值：投递统计。
        异常情况：事件名或 selection 非法时抛出 `ValueError`。
        """
        self._validate_event(event)
        self.recorder.record_event(event)
        selected, route = self._resolve_matching_devices_with_diagnostics(
            event,
            selection=selection,
        )
        result = self._deliver_to_devices(event, selected, route_diagnostics=route)
        self._record_route(event, result)
        return result

    def resolve_matching_devices(
        self,
        event: Event,
        *,
        selection: str = "all",
    ) -> list[Device]:
        """按订阅和选择策略解析目标设备。

        主要逻辑：使用协议订阅匹配候选设备，再应用 `all` 或 `first_available`。
        该方法不投递事件，供 Stream Service 在创建 output stream 前冻结 consumer
        列表。
        参数：`event` 为协议事件，`selection` 为选择策略。
        返回值：最终匹配的设备记录列表。
        异常情况：事件名或 selection 非法时抛出 `ValueError`。
        """
        self._validate_event(event)
        selected, _ = self._resolve_matching_devices_with_diagnostics(
            event,
            selection=selection,
        )
        return selected

    def _deliver_to_devices(
        self,
        event: Event,
        devices: list[Device],
        *,
        route_diagnostics: list[dict[str, Any]] | None = None,
    ) -> PublishResult:
        failed: list[str] = []
        delivered = 0
        diagnostics = [dict(item) for item in (route_diagnostics or [])]
        by_device = {item.get("device_id"): item for item in diagnostics}
        for device in devices:
            connection = self._connections.get(device.device_id)
            if connection is None:
                failed.append(device.device_id)
                self._mark_route_delivery(by_device, device.device_id, delivered=False, reason="connection_missing")
                continue
            try:
                connection.push_event(event)
                delivered += 1
                self._mark_route_delivery(by_device, device.device_id, delivered=True, reason="delivered")
            except Exception as exc:
                failed.append(device.device_id)
                device.connection_state = "offline"
                self._record_device_error(device, "event_delivery_failed", str(exc))
                self._mark_route_delivery(by_device, device.device_id, delivered=False, reason="event_delivery_failed")
        return PublishResult(
            matched_count=len(devices),
            delivered_count=delivered,
            failed_device_ids=tuple(failed),
            matched_device_ids=tuple(device.device_id for device in devices),
            route_diagnostics=tuple(diagnostics),
        )

    def _push_event_to_device_ids(
        self,
        event: Event,
        device_ids: tuple[str, ...],
        *,
        route_reason: str = "frozen_stream_consumer",
    ) -> PublishResult:
        """向已冻结的内部设备集合推送事件。

        主要逻辑：供 Stream Service 向已冻结 output consumer 或 input producer
        推送生命周期事件，不向 Tool / Task 暴露 device_id 点对点发送接口。
        参数：`event` 为协议事件，`device_ids` 为内部冻结 consumer 列表。
        `route_reason` 为路由诊断原因。
        返回值：投递统计。
        异常情况：事件非法时抛出 `ValueError`。
        """
        self._validate_event(event)
        self.recorder.record_event(event)
        devices = [self._devices[device_id] for device_id in device_ids if device_id in self._devices]
        route = [
            self._route_decision(device, route_matched=True, selected=True, reason=route_reason)
            for device in devices
        ]
        result = self._deliver_to_devices(event, devices, route_diagnostics=route)
        self._record_route(event, result)
        return result

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

    def resolve_recipients(self, event: Event) -> list[Device]:
        result, _ = self._resolve_recipients_with_diagnostics(event)
        return result

    def _resolve_recipients_with_diagnostics(self, event: Event) -> tuple[list[Device], list[dict[str, Any]]]:
        """解析订阅者并生成路由诊断。

        主要逻辑：只检查当前 user 下设备，依次过滤离线设备、事件生产者和订阅规则。
        诊断用于 debug API、runs 产物和 Tool 返回值，帮助开发者理解为什么某台设备
        收到或没有收到事件。
        参数：`event` 为待发布事件。
        返回值：`(命中设备列表, 诊断列表)`。
        异常情况：无。
        """

        result: list[Device] = []
        diagnostics: list[dict[str, Any]] = []
        for device in self._devices.values():
            if device.user_id != event.user_id:
                continue
            if device.connection_state != "online":
                diagnostics.append(self._route_decision(device, route_matched=False, selected=False, reason="device_offline"))
                continue
            if self.exclude_producer_by_default and device.device_id == event.producer_id:
                diagnostics.append(self._route_decision(device, route_matched=False, selected=False, reason="producer_excluded"))
                continue
            match = self._first_route_match(event, device)
            if match["matched"]:
                result.append(device)
                diagnostics.append(
                    self._route_decision(
                        device,
                        route_matched=True,
                        selected=True,
                        reason="route_matched",
                        route=match.get("route"),
                        filter_data=match.get("filter"),
                    )
                )
            else:
                diagnostics.append(
                    self._route_decision(
                        device,
                        route_matched=False,
                        selected=False,
                        reason=str(match.get("reason") or "route_mismatch"),
                        route=match.get("route"),
                        filter_data=match.get("filter"),
                        detail={
                            key: match[key]
                            for key in ("path", "expected", "actual")
                            if key in match
                        },
                    )
                )
        return result, diagnostics

    def _resolve_matching_devices_with_diagnostics(
        self,
        event: Event,
        *,
        selection: str,
    ) -> tuple[list[Device], list[dict[str, Any]]]:
        """按订阅和选择策略解析设备，并保留每一步诊断。"""

        if selection not in {"all", "first_available"}:
            raise ValueError(f"unsupported device selection: {selection}")
        candidates, diagnostics = self._resolve_recipients_with_diagnostics(event)
        selected: list[Device] = []
        for device in candidates:
            if selection == "first_available" and selected:
                self._mark_route_selected(
                    diagnostics,
                    device.device_id,
                    selected=False,
                    reason="selection_skipped",
                )
                continue
            selected.append(device)
            self._mark_route_selected(
                diagnostics,
                device.device_id,
                selected=True,
                reason="selected",
            )
        return selected, diagnostics

    def _first_route_match(self, event: Event, device: Device) -> dict[str, Any]:
        """返回设备内部路由对事件的首个匹配结果或最有用的失败结果。"""

        first_miss: dict[str, Any] | None = None
        filter_miss: dict[str, Any] | None = None
        for route in device.routes:
            current = self.matcher.explain(event, route, device)
            if current["matched"]:
                return current
            first_miss = first_miss or current
            if current.get("reason") == "filter_mismatch":
                filter_miss = filter_miss or current
        return filter_miss or first_miss or {"matched": False, "reason": "no_route"}

    @staticmethod
    def _route_decision(
        device: Device,
        *,
        route_matched: bool,
        selected: bool,
        reason: str,
        route: Any = None,
        filter_data: Any = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构造一条设备路由诊断。"""

        decision = {
            "device_id": device.device_id,
            "name": device.name,
            "connection_state": device.connection_state,
            "route_matched": route_matched,
            "selected": selected,
            "delivered": False,
            "reason": reason,
        }
        if route is not None:
            decision["route"] = route
        if filter_data:
            decision["filter"] = dict(filter_data)
        if detail:
            decision["detail"] = dict(detail)
        return decision

    @staticmethod
    def _mark_route_selected(
        diagnostics: list[dict[str, Any]],
        device_id: str,
        *,
        selected: bool,
        reason: str,
    ) -> None:
        """更新某台设备的选择结果。"""

        for item in diagnostics:
            if item.get("device_id") != device_id:
                continue
            item["selected"] = selected
            item["reason"] = reason
            return

    @staticmethod
    def _mark_route_delivery(
        by_device: dict[Any, dict[str, Any]],
        device_id: str,
        *,
        delivered: bool,
        reason: str,
    ) -> None:
        """更新某台设备的投递结果。"""

        item = by_device.get(device_id)
        if item is None:
            return
        item["delivered"] = delivered
        item["delivery_reason"] = reason

    def _record_route(self, event: Event, result: PublishResult) -> None:
        """记录事件路由诊断。"""

        recorder = getattr(self, "recorder", None)
        if recorder is None or not hasattr(recorder, "record_event_route"):
            return
        recorder.record_event_route(
            event,
            {
                "event": "event.route.resolved",
                "event_name": event.event_name,
                "matched_count": result.matched_count,
                "delivered_count": result.delivered_count,
                "failed_device_ids": list(result.failed_device_ids),
                "matched_device_ids": list(result.matched_device_ids),
                "route_diagnostics": list(result.route_diagnostics),
            },
        )

    def get_active_device_set(self, user_id: str) -> ActiveDeviceSet:
        devices = tuple(
            device
            for device in self._devices.values()
            if device.user_id == user_id and device.connection_state == "online"
        )
        return ActiveDeviceSet(user_id=user_id, devices=devices)

    def append_message(self, user_id: str, message: dict[str, Any]) -> None:
        session_id = str(message.get("session_id") or message.get("device_id") or "")
        if self.conversation_memory is not None and session_id:
            self.conversation_memory.append_message(user_id=user_id, device_id=session_id, message=message)
            self.recorder.log_message(
                user_id,
                message,
                detail_path=self.conversation_memory.legacy_messages_path(user_id=user_id, device_id=session_id),
            )
            return
        self.recorder.record_message(user_id, message)

    def load_messages(self, *, user_id: str, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """读取同一用户同一设备的历史消息。

        主要逻辑：优先委托 ConversationMemoryService 读取 active messages；未配置时
        回退到 RunRecorder 的旧 `messages.jsonl` 读取逻辑。
        参数：`user_id` 为用户编号，`session_id` 为设备级会话编号，`limit` 为最大条数。
        返回值：历史消息列表。
        异常情况：底层无读取能力时返回空列表。
        """

        if self.conversation_memory is not None:
            return self.conversation_memory.load_active_messages(user_id=user_id, device_id=session_id, limit=limit)
        loader = getattr(self.recorder, "load_messages", None)
        if not callable(loader):
            return []
        return loader(user_id=user_id, session_id=session_id, limit=limit)

    def load_message_summary_fragment(self, *, user_id: str, session_id: str) -> str:
        """读取可注入模型上下文的最近历史摘要片段。"""

        if self.conversation_memory is None:
            return ""
        return self.conversation_memory.build_summary_prompt_fragment(user_id=user_id, device_id=session_id)

    def compact_messages_if_needed(
        self,
        *,
        user_id: str,
        session_id: str,
        threshold: int = 30,
        keep_latest: int = 5,
    ) -> MessageSummary | None:
        """在连续对话结束时按阈值压缩 active messages。"""

        if self.conversation_memory is None:
            return None
        return self.conversation_memory.compact_if_needed(
            user_id=user_id,
            device_id=session_id,
            threshold=threshold,
            keep_latest=keep_latest,
        )

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
            name=device.name,
            device_name=device.device_name,
            client_type=device.client_type,
            sdk_version=device.sdk_version,
            properties=dict(device.properties),
            connection_id=device.connection_id,
            last_seen_at=device.last_seen_at,
            connection_state=device.connection_state,
            routes=tuple(device.routes),
            last_error=dict(device.last_error) if device.last_error is not None else None,
            register_failed_reason=device.register_failed_reason,
            auth_diagnostics=dict(device.auth_diagnostics),
            binding_diagnostics=dict(device.binding_diagnostics),
        )

    @staticmethod
    def _validate_event(event: Event) -> None:
        validate_event_name(event.event_name)
        validate_control_event_payload(event.payload)
        if not is_allowed_event_name(event.event_name):
            raise ValueError(f"unknown event_name: {event.event_name}")
        if not event.user_id or not event.producer_id:
            raise ValueError("event requires user_id and producer_id")
        if any(key in event.payload for key in ("target_device", "target_device_id", "source_device", "source_device_id")):
            raise ValueError("event payload must not contain target/source device fields")

    @staticmethod
    def _record_device_error(device: Device, code: str, message: str) -> None:
        device.last_error = {"code": code, "message": message, "timestamp_ms": int(time.time() * 1000)}
