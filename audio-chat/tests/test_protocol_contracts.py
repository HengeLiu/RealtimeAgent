from audio_chat.protocol import Event, StreamChunk, StreamChunkCodec


def test_event_envelope_uses_audio_chat_v1_without_target_fields() -> None:
    event = Event(
        event_name="stream.output.open.requested",
        user_id="user-001",
        producer_id="server-main",
        stream_id="stream_out_001",
        stream_type="actuator.speaker",
        payload={"stream_type": "actuator.speaker"},
    )

    data = event.to_dict()

    assert data["version"] == "audio-chat.v1"
    assert data["event_name"] == "stream.output.open.requested"
    assert all(not key.endswith("_device_id") for key in data)


def test_stream_chunk_codec_round_trips_internal_binary_slice() -> None:
    chunk = StreamChunk(
        user_id="user-001",
        session_id="sess-001",
        stream_id="stream_in_001",
        stream_type="sensor.mic",
        seq=7,
        payload=b"abc",
        final=True,
    )

    decoded = StreamChunkCodec.decode(StreamChunkCodec.encode(chunk))

    assert decoded == chunk
