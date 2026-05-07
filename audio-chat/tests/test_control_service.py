from audio_chat.control import ControlService, DeviceAuthenticator
from audio_chat.protocol import Event


class FakeConnection:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events = []
        self.chunks = []

    def push_event(self, event: Event) -> None:
        self.events.append(event)

    def push_stream_chunk(self, chunk: object) -> None:
        self.chunks.append(chunk)

    def close(self, *, reason: str) -> None:
        self.events.append(Event(event_name="control.device.state.changed", user_id="user-001", producer_id="server-main", payload={"reason": reason}))


def _registration(device_id: str, subscriptions: list[dict]) -> Event:
    return Event(
        event_name="control.device.register.requested",
        user_id="user-001",
        producer_id=device_id,
        payload={
            "device_id": device_id,
            "device_name": device_id,
            "client_type": "python-playback",
            "sdk_version": "audio-chat-endpoint-0.1.0",
            "auth": {"mode": "disabled"},
            "capabilities": {
                "streams.produce": ["sensor.mic"],
                "streams.consume": ["actuator.speaker"],
            },
            "subscriptions": subscriptions,
        },
    )


def _registration_for_user(user_id: str, device_id: str, subscriptions: list[dict], *, auth: dict | None = None) -> Event:
    event = _registration(device_id, subscriptions)
    payload = dict(event.payload)
    payload["auth"] = auth or {"mode": "disabled"}
    return Event(
        event_name=event.event_name,
        user_id=user_id,
        producer_id=device_id,
        payload=payload,
    )


def test_register_device_adds_active_device_set_and_binding() -> None:
    service = ControlService()
    connection = FakeConnection("dev-001")

    response = service.register_device(
        _registration("dev-001", [{"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}}]),
        connection,
    )

    assert response.event_name == "control.device.registered"
    active = service.get_active_device_set("user-001")
    assert [device.device_id for device in active.devices] == ["dev-001"]


def test_publish_resolves_by_subscription() -> None:
    service = ControlService()
    speaker = FakeConnection("speaker")
    sensor = FakeConnection("sensor")
    service.register_device(
        _registration("speaker", [{"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}}]),
        speaker,
    )
    service.register_device(
        _registration("sensor", [{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}]),
        sensor,
    )

    result = service.publish(
        Event(
            event_name="stream.output.open.requested",
            user_id="user-001",
            producer_id="server-main",
            stream_type="actuator.speaker",
            payload={"stream_type": "actuator.speaker"},
        )
    )

    assert result.matched_count == 1
    assert result.delivered_count == 1
    assert [event.event_name for event in speaker.events] == ["stream.output.open.requested"]
    assert sensor.events == []


def test_control_service_public_publish_does_not_accept_target_device_id() -> None:
    """测试目标：确认业务侧不能通过 ControlService 公共 API 点对点发送事件。

    测试方法：检查公共对象没有 `publish_to_device` 方法，普通 publish 只接收 Event。
    预期结果：公共点对点方法不存在；协议原生选择分发由 `publish_matching()` 承担。
    """
    service = ControlService()

    assert not hasattr(service, "publish_to_device")
    assert hasattr(service, "publish_matching")


def test_heartbeat_and_disconnect_update_debug_snapshot() -> None:
    """测试目标：验证真实控制连接的心跳和断线状态会反映到 debug snapshot。

    测试方法：注册设备后发送 `control.device.heartbeat.received`，再标记连接断开。
    预期结果：snapshot 包含 connection_id、last_seen_at，并在断开后显示 offline。
    """
    service = ControlService()
    connection = FakeConnection("dev-001")
    registered = service.register_device(_registration("dev-001", [{"event": "control.audio_session.*"}]), connection)

    service.record_heartbeat(
        Event(
            event_name="control.device.heartbeat.received",
            user_id="user-001",
            producer_id="dev-001",
            payload={"connection_id": registered.payload["connection_id"]},
        )
    )
    online_snapshot = service.build_devices_snapshot()["devices"][0]
    service.mark_connection_offline("dev-001", connection_id=registered.payload["connection_id"], reason="test")
    offline_snapshot = service.build_devices_snapshot()["devices"][0]

    assert online_snapshot["connection_id"] == registered.payload["connection_id"]
    assert online_snapshot["last_seen_at"] > 0
    assert offline_snapshot["connection_state"] == "offline"


def test_registration_validator_applies_subscription_config() -> None:
    """测试目标：验证 subscription 相关 YAML 配置会进入注册校验。

    测试方法：限制每设备最多 1 个订阅，并禁止 `*` 订阅。
    预期结果：超量订阅或全量订阅都会注册失败。
    """
    service = ControlService(max_subscriptions_per_device=1, allow_subscribe_all=False)

    too_many = service.register_device(
        _registration(
            "dev-many",
            [
                {"event": "control.audio_session.*"},
                {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
            ],
        ),
        FakeConnection("dev-many"),
    )
    wildcard = service.register_device(_registration("dev-wild", [{"event": "*"}]), FakeConnection("dev-wild"))

    assert too_many.event_name == "control.device.register.failed"
    assert wildcard.event_name == "control.device.register.failed"


def test_subscription_filter_matches_envelope_payload_capabilities_and_arrays() -> None:
    """测试目标：验证 A 线要求的订阅 filter 能匹配信封、payload 和 capabilities。

    测试方法：注册一个同时过滤 producer_id、payload.command_name 和
    capabilities.streams.consume 的设备，再发布匹配事件。
    预期结果：数组包含匹配生效，事件只投递给符合能力和订阅条件的设备。
    """

    service = ControlService()
    endpoint = FakeConnection("dev-speaker")
    service.register_device(
        _registration(
            "dev-speaker",
            [
                {
                    "event": "control.device.command.requested",
                    "filter": {
                        "producer_id": "server-main",
                        "payload.command_name": "audio.play",
                        "capabilities.streams.consume": "actuator.speaker",
                    },
                }
            ],
        ),
        endpoint,
    )

    result = service.publish(
        Event(
            event_name="control.device.command.requested",
            user_id="user-001",
            producer_id="server-main",
            payload={"command_name": "audio.play"},
        )
    )

    assert result.matched_device_ids == ("dev-speaker",)
    assert [event.payload["command_name"] for event in endpoint.events] == ["audio.play"]


def test_subscription_filter_rejects_regex_and_unknown_paths() -> None:
    """测试目标：明确 filter 不支持脚本、正则和复杂表达式。

    测试方法：注册包含正则样式路径和未知顶层路径的订阅。
    预期结果：注册失败，并在 debug snapshot 中保留失败原因。
    """

    service = ControlService()
    regex = service.register_device(
        _registration("dev-regex", [{"event": "stream.output.*", "filter": {"payload.name|regex": "x"}}]),
        FakeConnection("dev-regex"),
    )
    unknown = service.register_device(
        _registration("dev-unknown", [{"event": "stream.output.*", "filter": {"device_id": "dev-001"}}]),
        FakeConnection("dev-unknown"),
    )

    assert regex.event_name == "control.device.register.failed"
    assert unknown.event_name == "control.device.register.failed"
    assert "unsupported subscription filter" in service.build_device_snapshot("dev-regex")["register_failed_reason"]


def test_static_token_binding_reconnect_and_cross_user_rejection() -> None:
    """测试目标：验证 static_token、同设备重连覆盖旧连接和跨 user 绑定保护。

    测试方法：用正确 token 注册设备，再用同 user 重连，最后换 user 尝试绑定同设备。
    预期结果：旧连接关闭，active device set 仍只有新连接；跨 user 注册失败。
    """

    service = ControlService(authenticator=DeviceAuthenticator(mode="static_token", device_tokens={"dev-auth": "secret"}))
    first = FakeConnection("dev-auth")
    second = FakeConnection("dev-auth")
    ok = service.register_device(
        _registration_for_user(
            "user-001",
            "dev-auth",
            [{"event": "control.audio_session.*"}],
            auth={"mode": "static_token", "token": "secret"},
        ),
        first,
    )
    reconnect = service.register_device(
        _registration_for_user(
            "user-001",
            "dev-auth",
            [{"event": "control.audio_session.*"}],
            auth={"mode": "static_token", "token": "secret"},
        ),
        second,
    )
    denied = service.register_device(
        _registration_for_user(
            "user-002",
            "dev-auth",
            [{"event": "control.audio_session.*"}],
            auth={"mode": "static_token", "token": "secret"},
        ),
        FakeConnection("dev-auth"),
    )

    assert ok.event_name == "control.device.registered"
    assert reconnect.event_name == "control.device.registered"
    assert first.events[-1].payload["reason"] == "replaced_by_new_connection"
    assert denied.event_name == "control.device.register.failed"
    assert "device_bound_to_other_user" in denied.payload["reason"]


def test_signed_token_reports_missing_secret_reason() -> None:
    """测试目标：验证 signed_token 缺少服务端签名密钥时给出明确失败提示。

    测试方法：开启 signed_token 鉴权但不设置密钥环境变量。
    预期结果：注册失败原因是 `signed_token_secret_missing`，不是静默降级。
    """

    service = ControlService(authenticator=DeviceAuthenticator(mode="signed_token"))
    response = service.register_device(
        _registration_for_user(
            "user-001",
            "dev-signed",
            [{"event": "control.audio_session.*"}],
            auth={"mode": "signed_token", "token": "abc"},
        ),
        FakeConnection("dev-signed"),
    )

    assert response.event_name == "control.device.register.failed"
    assert response.payload["reason"] == "signed_token_secret_missing"


def test_heartbeat_timeout_marks_device_offline_and_records_recent_error() -> None:
    """测试目标：验证 active device set 中的心跳超时会标记设备离线。

    测试方法：注册设备后把 now 推进超过 timeout，再调用 `expire_stale_devices()`。
    预期结果：设备从 active set 移除，debug snapshot 中有 `heartbeat_timeout` 最近错误。
    """

    service = ControlService()
    service.register_device(_registration("dev-timeout", [{"event": "control.audio_session.*"}]), FakeConnection("dev-timeout"))
    snapshot = service.build_device_snapshot("dev-timeout")

    expired = service.expire_stale_devices(now=snapshot["last_seen_at"] + 31, timeout_seconds=30)

    assert expired == ("dev-timeout",)
    assert service.get_active_device_set("user-001").devices == ()
    assert service.build_device_snapshot("dev-timeout")["last_error"]["code"] == "heartbeat_timeout"
