from __future__ import annotations

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunk
from audio_chat.tools import UserDeviceContext


class RecordingEndpoint:
    def __init__(self, *, user_id: str, device_id: str) -> None:
        self.user_id = user_id
        self.device_id = device_id
        self.events: list[Event] = []
        self.chunks: list[StreamChunk] = []
        self.closed_reasons: list[str] = []

    def push_event(self, event: Event) -> None:
        self.events.append(event)

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        self.chunks.append(chunk)

    def close(self, *, reason: str) -> None:
        self.closed_reasons.append(reason)


def register_endpoint(
    app: AudioChatApp,
    endpoint: RecordingEndpoint,
    *,
    subscriptions: list[dict],
    properties: dict | None = None,
) -> None:
    response = app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=endpoint.user_id,
            producer_id=endpoint.device_id,
            payload={
                "device_id": endpoint.device_id,
                "device_name": endpoint.device_id,
                "client_type": "acceptance",
                "sdk_version": "audio-chat-endpoint-0.1.0",
                "auth": {"mode": "disabled"},
                "subscriptions": subscriptions,
                "properties": properties or {},
            },
        ),
        endpoint,
    )
    assert response.event_name == "control.device.registered"


def test_public_tool_task_api_does_not_export_second_layer_request_objects() -> None:
    """Tool/Task developers should only see protocol-native APIs."""
    import audio_chat
    import audio_chat.output as output
    import audio_chat.tools as tools

    forbidden_names = {
        "DeviceControlRequest",
        "AssetRequest",
        "StreamControlRequest",
        "StreamOpenRequest",
        "OutputIntent",
    }

    for name in forbidden_names:
        assert not hasattr(audio_chat, name)
        assert not hasattr(tools, name)
        assert not hasattr(output, name)


def test_asset_request_routes_by_stream_subscription(tmp_path) -> None:
    """资产请求只依赖 stream 控制订阅，不要求设备重复声明 capabilities。

    测试目标：验证设备能力事实来自 subscriptions。
    测试方法：注册一个订阅 `sensor.rgb` 的设备和一个只订阅 `sensor.imu` 的设备，
    然后请求 `sensor.rgb` 资产。
    预期结果：事件只投递给 `sensor.rgb` 订阅设备。
    """
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), asset_request_timeout_seconds=0.01))
    user_id = "user-asset-routing"
    rgb_device = RecordingEndpoint(user_id=user_id, device_id="dev-rgb")
    imu_device = RecordingEndpoint(user_id=user_id, device_id="dev-imu")

    register_endpoint(
        app,
        rgb_device,
        subscriptions=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
    )
    register_endpoint(
        app,
        imu_device,
        subscriptions=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.imu"}}],
    )

    asset = UserDeviceContext(user_id=user_id, app=app).request_asset(
        "sensor.rgb",
        freshness_seconds=0,
        configure_payload={"mode": "single", "max_samples": 1},
        timeout_seconds=0.01,
    )

    assert asset is None
    assert [event.event_name for event in rgb_device.events] == ["stream.control.configure.requested"]
    assert imu_device.events == []


def test_open_output_stream_honors_capability_and_selection(tmp_path) -> None:
    """Output streams must be routed by capability/subscription, not every subscriber."""
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    user_id = "user-output-routing"
    first = RecordingEndpoint(user_id=user_id, device_id="dev-haptic-1")
    second = RecordingEndpoint(user_id=user_id, device_id="dev-haptic-2")
    speaker_only = RecordingEndpoint(user_id=user_id, device_id="dev-speaker-only")

    for endpoint in (first, second):
        register_endpoint(
            app,
            endpoint,
            subscriptions=[{"event": "stream.output.*", "filter": {"stream_type": "actuator.haptic"}}],
        )
    register_endpoint(
        app,
        speaker_only,
        subscriptions=[{"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}}],
    )

    writer = UserDeviceContext(user_id=user_id, app=app).open_output_stream(
        "actuator.haptic",
        codec="raw",
        require_capability="actuator.haptic",
        selection="first_available",
    )
    writer.write(b"\x01\x02", final=True)

    assert [event.event_name for event in first.events][:2] == [
        "stream.output.open.requested",
        "stream.output.close.requested",
    ]
    assert [chunk.payload for chunk in first.chunks] == [b"\x01\x02"]
    assert second.events == []
    assert second.chunks == []
    assert speaker_only.events == []
    assert speaker_only.chunks == []


def test_payload_only_control_event_does_not_open_stream(tmp_path) -> None:
    """Small control payloads should stay as events and not create streams."""
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    user_id = "user-payload-only"
    endpoint = RecordingEndpoint(user_id=user_id, device_id="dev-nav")
    register_endpoint(
        app,
        endpoint,
        subscriptions=[
            {
                "event": "control.device.command.requested",
                "filter": {"payload.command_name": "navigation.start"},
            }
        ],
        properties={"navigation.endpoint": True},
    )

    result = UserDeviceContext(user_id=user_id, app=app).publish_event(
        "control.device.command.requested",
        payload={"command_name": "navigation.start", "params": {"destination": "office"}},
        require_capability="navigation.endpoint",
        selection="first_available",
    )

    assert result.matched_count == 1
    assert result.delivered_count == 1
    assert len(endpoint.events) == 1
    assert endpoint.events[0].payload["params"]["destination"] == "office"
    assert app.stream_service.registry.list_by_user(user_id) == []


def test_debug_snapshot_explains_subscription_miss_and_recent_errors(tmp_path) -> None:
    """设备 debug snapshot 必须能解释事件路由和最近失败。

    测试方法：注册一个只订阅 `sensor.rgb` 的设备，然后发布 `sensor.depth` 配置事件。
    再主动触发心跳超时。
    预期结果：事件未投递；用户快照保留订阅 filter；设备快照记录心跳超时错误。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = RecordingEndpoint(user_id="user-debug", device_id="dev-rgb")
    register_endpoint(
        app,
        endpoint,
        subscriptions=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
    )

    result = UserDeviceContext(user_id="user-debug", app=app).publish_event(
        "stream.control.configure.requested",
        payload={"stream_type": "sensor.depth"},
        stream_type="sensor.depth",
    )
    snapshot = app.control_service.build_user_snapshot("user-debug")
    device = snapshot["devices"][0]
    route_events = (tmp_path / "runs" / "control-routes.jsonl").read_text(encoding="utf-8")
    app.control_service.expire_stale_devices(now=device["last_seen_at"] + 31, timeout_seconds=30)

    assert result.matched_count == 0
    assert endpoint.events == []
    assert "event.route.resolved" in route_events
    assert "filter_mismatch" in route_events
    assert device["subscriptions"][0]["filter"] == {"stream_type": "sensor.rgb"}
    assert app.control_service.build_device_snapshot("dev-rgb")["last_error"]["code"] == "heartbeat_timeout"
