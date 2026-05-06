from audio_chat.control import ControlService
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

    测试方法：检查公共对象没有 `publish_to_device` 方法，普通 publish 只接收 Event；
    受控定向能力保留在内部 `_push_to_resolved_device`，只供 DeviceCommandService 协作使用。
    预期结果：公共点对点方法不存在，内部协作方法存在。
    """
    service = ControlService()

    assert not hasattr(service, "publish_to_device")
    assert hasattr(service, "_push_to_resolved_device")


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
