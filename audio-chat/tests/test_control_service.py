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
