from __future__ import annotations

import asyncio

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunk
from audio_chat.tools import UserDeviceContext


class EndpointProbe:
    def __init__(self, *, app: AudioChatApp, user_id: str, device_id: str) -> None:
        self.app = app
        self.user_id = user_id
        self.device_id = device_id
        self.events: list[Event] = []
        self.chunks: list[StreamChunk] = []
        self.closed_reasons: list[str] = []

    def push_event(self, event: Event) -> None:
        self.events.append(event)
        if (
            event.event_name == "stream.control.configure.requested"
            and event.stream_type == "sensor.rgb"
            and event.payload.get("mode") == "continuous"
        ):
            self._upload_rgb_frames(event.payload["correlation_id"])

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        self.chunks.append(chunk)

    def close(self, *, reason: str) -> None:
        self.closed_reasons.append(reason)

    def _upload_rgb_frames(self, correlation_id: str) -> None:
        handle = self.app.open_input_stream(
            user_id=self.user_id,
            producer_id=self.device_id,
            stream_type="sensor.rgb",
        )
        for seq in range(3):
            self.app.write_input_chunk(
                StreamChunk(
                    user_id=self.user_id,
                    session_id=handle.session_id,
                    stream_id=handle.stream_id,
                    stream_type="sensor.rgb",
                    seq=seq,
                    payload=f"rgb-frame-{seq}".encode(),
                    metadata={"correlation_id": correlation_id},
                )
            )


def register_endpoint(
    app: AudioChatApp,
    endpoint: EndpointProbe,
    *,
    capabilities: dict,
    subscriptions: list[dict],
) -> None:
    registered = app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=endpoint.user_id,
            producer_id=endpoint.device_id,
            payload={
                "device_id": endpoint.device_id,
                "device_name": endpoint.device_id,
                "client_type": "acceptance-probe",
                "sdk_version": "audio-chat-endpoint-0.1.0",
                "auth": {"mode": "disabled"},
                "capabilities": capabilities,
                "subscriptions": subscriptions,
            },
        ),
        endpoint,
    )
    assert registered.event_name == "control.device.registered"


def test_payload_only_device_command_uses_event_without_stream(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = EndpointProbe(app=app, user_id="user-command", device_id="dev-navigation")
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

    result = UserDeviceContext(user_id="user-command", app=app).publish_event(
        "control.device.command.requested",
        payload={
            "command_name": "navigation.start",
            "params": {"destination": "gate-a", "mode": "walking"},
        },
        selection="first_available",
    )

    assert result.matched_count == 1
    assert result.delivered_count == 1
    assert [event.event_name for event in endpoint.events] == ["control.device.command.requested"]
    assert endpoint.events[0].payload["params"]["destination"] == "gate-a"
    assert app.stream_service.registry.list_by_user("user-command") == []


def test_continuous_sensor_task_uses_config_event_stream_and_asset_watch(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = EndpointProbe(app=app, user_id="user-video", device_id="dev-camera")
    register_endpoint(
        app,
        endpoint,
        capabilities={"streams.produce": ["sensor.rgb"], "sensor.rgb": True},
        subscriptions=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
    )
    context = UserDeviceContext(user_id="user-video", app=app)

    result = context.publish_event(
        "stream.control.configure.requested",
        stream_type="sensor.rgb",
        payload={
            "mode": "continuous",
            "fps": 2,
            "format": "jpeg",
            "asset_policy": "cache",
            "correlation_id": "task-video-42",
        },
        selection="first_available",
    )

    async def collect_frames() -> list:
        frames = []
        async for ref in context.watch_assets(
            stream_type="sensor.rgb",
            correlation_id="task-video-42",
            timeout_seconds=1,
        ):
            frames.append(ref)
            if len(frames) == 3:
                break
        return frames

    frames = asyncio.run(collect_frames())

    assert result.delivered_count == 1
    assert [frame.metadata["seq"] for frame in frames] == [0, 1, 2]
    assert all(frame.metadata["correlation_id"] == "task-video-42" for frame in frames)
    assert app.stream_service.registry.list_by_user("user-video")[0].stream_type == "sensor.rgb"

    stop = context.publish_event(
        "stream.control.configure.requested",
        stream_type="sensor.rgb",
        payload={"mode": "stop", "correlation_id": "task-video-42"},
        selection="first_available",
    )
    assert stop.delivered_count == 1
    assert endpoint.events[-1].payload == {"mode": "stop", "correlation_id": "task-video-42"}


def test_actuator_bytes_use_output_stream_not_event_payload(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = EndpointProbe(app=app, user_id="user-haptic", device_id="dev-haptic")
    register_endpoint(
        app,
        endpoint,
        capabilities={"streams.consume": ["actuator.haptic"], "actuator.haptic": True},
        subscriptions=[{"event": "stream.output.*", "filter": {"stream_type": "actuator.haptic"}}],
    )

    writer = UserDeviceContext(user_id="user-haptic", app=app).open_output_stream(
        "actuator.haptic",
        codec="raw",
        selection="first_available",
    )
    writer.write(b"\x01\x80\x40", final=True)

    assert [event.event_name for event in endpoint.events] == [
        "stream.output.open.requested",
        "stream.output.close.requested",
    ]
    assert endpoint.events[0].payload["stream_type"] == "actuator.haptic"
    assert "data" not in endpoint.events[0].payload
    assert [chunk.payload for chunk in endpoint.chunks] == [b"\x01\x80\x40"]
