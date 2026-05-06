from audio_chat.agent_core import TextAgentCore
from audio_chat.audio_pipeline import AudioPipeline, FormatNormalizer
from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunk


def test_audio_pipeline_rejects_non_mic_stream() -> None:
    app = AudioChatApp(AudioChatConfig(runs_root="audio-chat/runs/test-audio-pipeline"))
    pipeline = AudioPipeline(text_agent_core=app.text_agent_core)
    chunk = StreamChunk(
        user_id="user-001",
        session_id="sess-001",
        stream_id="stream-001",
        stream_type="sensor.rgb",
        seq=0,
        payload=b"not-audio",
    )

    try:
        pipeline.process(chunk)
    except ValueError as exc:
        assert "sensor.mic" in str(exc)
    else:
        raise AssertionError("Audio Pipeline accepted non sensor.mic stream")


def test_format_normalizer_accepts_default_sensor_mic_format() -> None:
    normalizer = FormatNormalizer()
    chunk = StreamChunk(
        user_id="user-001",
        session_id="sess-001",
        stream_id="stream-001",
        stream_type="sensor.mic",
        seq=0,
        payload=b"\x00\x00",
    )

    assert normalizer.process(chunk) == chunk


def test_text_agent_core_final_mic_chunk_emits_output() -> None:
    app = AudioChatApp(AudioChatConfig(runs_root="audio-chat/runs/test-agent-core"))

    class Connection:
        device_id = "dev-playback"

        def __init__(self) -> None:
            self.events = []
            self.chunks = []

        def push_event(self, event: Event) -> None:
            self.events.append(event)

        def push_stream_chunk(self, chunk: StreamChunk) -> None:
            self.chunks.append(chunk)

    connection = Connection()
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-001",
            producer_id="dev-playback",
            payload={
                "device_id": "dev-playback",
                "auth": {"mode": "disabled"},
                "capabilities": {
                    "streams.produce": ["sensor.mic"],
                    "streams.consume": ["actuator.speaker"],
                },
                "subscriptions": [
                    {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
                ],
            },
        ),
        connection,
    )
    handle = app.open_input_stream(user_id="user-001", producer_id="dev-playback")

    app.write_input_chunk(
        StreamChunk(
            user_id="user-001",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x00\x00" * 320,
            final=True,
        )
    )

    assert any(event.event_name == "stream.output.open.requested" for event in connection.events)
    assert connection.chunks
