from api.handlers.audio_handler import AudioHandler
from protocol.enums import MessageType
from protocol.messages.envelope import Endpoint, Envelope


def _audio_message(name: str, *, requires_ack: bool = False) -> Envelope:
    return Envelope(
        message_id="msg_audio_1",
        trace_id="trace_audio_1",
        message_type=MessageType.COMMAND,
        message_name=name,
        protocol_version="1.0.0",
        source=Endpoint(device_id="dev_server_main", module="agent-core"),
        target=Endpoint(device_id="dev_glass_001", module="glass-api"),
        timestamp="2026-04-07T22:00:00+08:00",
        requires_ack=requires_ack,
        payload={"tts_text": "hello"},
    )


def test_audio_play_returns_started_and_finished_events() -> None:
    handler = AudioHandler()
    responses = handler.handle(_audio_message("audio.play"))

    assert [item.message_name for item in responses] == [
        "audio.play_started",
        "audio.play_finished",
    ]


def test_audio_stream_without_ack_returns_empty() -> None:
    handler = AudioHandler()
    responses = handler.handle(_audio_message("audio.stream"))
    assert responses == []
