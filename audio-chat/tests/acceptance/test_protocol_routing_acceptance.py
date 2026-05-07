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
    capabilities: dict,
    subscriptions: list[dict],
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
                "capabilities": capabilities,
                "subscriptions": subscriptions,
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


def test_asset_request_requires_capability_and_subscription(tmp_path) -> None:
    """Asset requests must not be delivered to devices that only subscribe by event.

    The architecture requires both:
    - capability says the device can produce the stream;
    - subscription says the device wants the configure event.
    """
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), asset_request_timeout_seconds=0.01))
    user_id = "user-asset-routing"
    capable = RecordingEndpoint(user_id=user_id, device_id="dev-rgb-capable")
    subscriber_only = RecordingEndpoint(user_id=user_id, device_id="dev-rgb-subscriber-only")

    register_endpoint(
        app,
        capable,
        capabilities={"streams.produce": ["sensor.rgb"], "sensor.rgb": True},
        subscriptions=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
    )
    register_endpoint(
        app,
        subscriber_only,
        capabilities={"streams.produce": ["sensor.imu"], "sensor.imu": True},
        subscriptions=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
    )

    asset = UserDeviceContext(user_id=user_id, app=app).request_asset(
        "sensor.rgb",
        freshness_seconds=0,
        configure_payload={"mode": "single", "max_samples": 1},
        timeout_seconds=0.01,
    )

    assert asset is None
    assert [event.event_name for event in capable.events] == ["stream.control.configure.requested"]
    assert subscriber_only.events == []


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
            capabilities={"streams.consume": ["actuator.haptic"], "actuator.haptic": True},
            subscriptions=[{"event": "stream.output.*", "filter": {"stream_type": "actuator.haptic"}}],
        )
    register_endpoint(
        app,
        speaker_only,
        capabilities={"streams.consume": ["actuator.speaker"], "actuator.speaker": True},
        subscriptions=[{"event": "stream.output.*", "filter": {"stream_type": "actuator.haptic"}}],
    )

    writer = UserDeviceContext(user_id=user_id, app=app).open_output_stream(
        "actuator.haptic",
        codec="raw",
        require_capability="actuator.haptic",
        selection="first_available",
    )
    writer.write(b"\x01\x02", final=True)

    assert [event.event_name for event in first.events] == [
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
        capabilities={"navigation.endpoint": True},
        subscriptions=[
            {
                "event": "control.device.command.requested",
                "filter": {"payload.command_name": "navigation.start"},
            }
        ],
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
