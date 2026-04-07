from api.handlers.camera_handler import CameraHandler
from protocol.enums import MessageType
from protocol.messages.envelope import Endpoint, Envelope


def _camera_message(name: str, *, requires_ack: bool = False) -> Envelope:
    return Envelope(
        message_id="msg_camera_1",
        trace_id="trace_camera_1",
        message_type=MessageType.COMMAND,
        message_name=name,
        protocol_version="1.0.0",
        source=Endpoint(device_id="dev_server_main", module="agent-core"),
        target=Endpoint(device_id="dev_glass_001", module="glass-api"),
        timestamp="2026-04-07T22:00:00+08:00",
        requires_ack=requires_ack,
        payload={"capture_mode": "single"},
    )


def test_camera_capture_returns_started_and_finished_events() -> None:
    handler = CameraHandler()
    responses = handler.handle(_camera_message("camera.capture"))

    assert [item.message_name for item in responses] == [
        "camera.capture_started",
        "camera.capture_finished",
    ]


def test_camera_non_capture_can_ack() -> None:
    handler = CameraHandler()
    responses = handler.handle(_camera_message("image.stream", requires_ack=True))

    assert len(responses) == 1
    assert responses[0].message_name == "camera.ack"
