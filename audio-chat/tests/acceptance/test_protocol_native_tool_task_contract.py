from __future__ import annotations

import asyncio

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import CONTROL_EVENTS, Event, StreamChunk
from audio_chat.tools import UserDeviceContext


class RecordingEndpoint:
    def __init__(self, *, app: AudioChatApp, user_id: str, device_id: str) -> None:
        self.app = app
        self.user_id = user_id
        self.device_id = device_id
        self.events: list[Event] = []
        self.output_chunks: list[StreamChunk] = []

    def push_event(self, event: Event) -> None:
        self.events.append(event)

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        self.output_chunks.append(chunk)

    def close(self, *, reason: str) -> None:
        pass


class ContinuousRgbEndpoint(RecordingEndpoint):
    def push_event(self, event: Event) -> None:
        super().push_event(event)
        if (
            event.event_name == "stream.control.configure.requested"
            and event.stream_type == "sensor.rgb"
            and event.payload.get("mode") == "continuous"
        ):
            handle = self.app.open_input_stream(
                user_id=self.user_id,
                producer_id=self.device_id,
                stream_type="sensor.rgb",
            )
            correlation_id = event.payload["correlation_id"]
            for seq in range(3):
                self.app.write_input_chunk(
                    StreamChunk(
                        user_id=self.user_id,
                        session_id=handle.session_id,
                        stream_id=handle.stream_id,
                        stream_type="sensor.rgb",
                        seq=seq,
                        payload=f"frame-{seq}".encode(),
                        codec="pcm16le",
                        metadata={"correlation_id": correlation_id},
                    )
                )


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
                "properties": dict(properties or {}),
                "subscriptions": subscriptions,
            },
        ),
        endpoint,
    )
    assert response.event_name == "control.device.registered"


def test_protocol_declares_device_command_events() -> None:
    assert "control.device.command.requested" in CONTROL_EVENTS
    assert "control.device.command.started" in CONTROL_EVENTS
    assert "control.device.command.completed" in CONTROL_EVENTS
    assert "control.device.command.failed" in CONTROL_EVENTS


def test_user_device_context_exposes_protocol_native_api_only(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    context = UserDeviceContext(user_id="user-api", app=app)

    assert hasattr(context, "publish_event")
    assert hasattr(context, "open_output_stream")
    assert hasattr(context, "request_asset")
    assert hasattr(context, "watch_assets")
    assert hasattr(context, "submit_text")
    assert hasattr(context, "submit_audio")

    assert not hasattr(context, "get_or_request_asset")
    assert not hasattr(context, "submit_output")

    # Tool/Task communication must go through protocol events and streams.
    assert not hasattr(context, "find_device")
    assert not hasattr(context, "publish_to_device")
    assert not hasattr(context, "open_device_stream")


def test_publish_event_broadcasts_by_subscription_not_device_id(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    first = RecordingEndpoint(app=app, user_id="user-broadcast", device_id="dev-rgb-1")
    second = RecordingEndpoint(app=app, user_id="user-broadcast", device_id="dev-rgb-2")
    other = RecordingEndpoint(app=app, user_id="user-broadcast", device_id="dev-haptic")

    for endpoint in (first, second):
        register_endpoint(
            app,
            endpoint,
            subscriptions=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
        )
    register_endpoint(
        app,
        other,
        subscriptions=[{"event": "stream.output.*", "filter": {"stream_type": "actuator.haptic"}}],
    )

    result = UserDeviceContext(user_id="user-broadcast", app=app).publish_event(
        "stream.control.configure.requested",
        stream_type="sensor.rgb",
        payload={"mode": "single", "max_samples": 1},
        selection="all",
    )

    assert result.matched_count == 2
    assert result.delivered_count == 2
    assert [event.event_name for event in first.events] == ["stream.control.configure.requested"]
    assert [event.event_name for event in second.events] == ["stream.control.configure.requested"]
    assert other.events == []
    assert "device_id" not in first.events[0].payload


def test_payload_only_control_event_does_not_open_stream(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = RecordingEndpoint(app=app, user_id="user-control", device_id="dev-nav")
    register_endpoint(
        app,
        endpoint,
        subscriptions=[
            {
                "event": "control.device.command.requested",
                "filter": {"payload.command_name": "navigation.start"},
            }
        ],
    )

    result = UserDeviceContext(user_id="user-control", app=app).publish_event(
        "control.device.command.requested",
        payload={
            "command_name": "navigation.start",
            "params": {"destination": "office", "mode": "walking"},
        },
        selection="first_available",
    )

    assert result.matched_count == 1
    assert result.delivered_count == 1
    assert len(endpoint.events) == 1
    assert endpoint.events[0].payload["params"]["destination"] == "office"
    assert app.stream_service.registry.list_by_user("user-control") == []


def test_continuous_sensor_stream_is_read_via_asset_watch(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = ContinuousRgbEndpoint(app=app, user_id="user-video", device_id="dev-camera")
    register_endpoint(
        app,
        endpoint,
        subscriptions=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
    )
    context = UserDeviceContext(user_id="user-video", app=app)
    correlation_id = "task-video-001"

    result = context.publish_event(
        "stream.control.configure.requested",
        stream_type="sensor.rgb",
        payload={
            "mode": "continuous",
            "fps": 2,
            "format": "jpeg",
            "asset_policy": "cache",
            "correlation_id": correlation_id,
        },
        selection="first_available",
    )
    assert result.delivered_count == 1

    async def collect() -> list:
        refs = []
        async for ref in context.watch_assets(
            stream_type="sensor.rgb",
            correlation_id=correlation_id,
            timeout_seconds=1,
        ):
            refs.append(ref)
            if len(refs) == 3:
                break
        return refs

    frames = asyncio.run(collect())

    assert [frame.metadata["seq"] for frame in frames] == [0, 1, 2]
    assert all(frame.metadata["correlation_id"] == correlation_id for frame in frames)
