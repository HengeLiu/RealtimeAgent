from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.output import AssistantTextDelta, OutputIntent
from audio_chat.protocol import Event, StreamChunk


class Connection:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events = []
        self.chunks = []

    def push_event(self, event: Event) -> None:
        self.events.append(event)

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        self.chunks.append(chunk)


def register_speaker(app: AudioChatApp, connection: Connection, user_id: str = "user-001") -> None:
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=connection.device_id,
            payload={
                "device_id": connection.device_id,
                "auth": {"mode": "disabled"},
                "capabilities": {
                    "streams.produce": ["sensor.mic"],
                    "streams.consume": ["actuator.speaker"],
                },
                "subscriptions": [
                    {"event": "control.audio_session.*"},
                    {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
                ],
            },
        ),
        connection,
    )


def test_text_agent_streams_text_and_tts_audio_before_final_done(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
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

    model_events = (tmp_path / "runs" / "sessions" / handle.session_id / "model-events.jsonl").read_text()
    assert model_events.index("assistant_audio.delta") < model_events.index('"final": true')
    assert model_events.count("assistant_text.delta") >= 2
    assert len(connection.chunks) >= 2


def test_playback_arbiter_interrupts_lower_priority_stream(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)

    low = OutputIntent(
        user_id="user-001",
        session_id="sess-output",
        priority="low",
        on_interrupted="drop",
        on_blocked="drop",
    )
    high = OutputIntent(
        user_id="user-001",
        session_id="sess-output",
        priority="critical",
        on_interrupted="drop",
        on_blocked="queue",
    )

    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-output", text="low priority", intent=low)
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-output-2", text="critical priority", intent=high)
    )

    decisions = (tmp_path / "runs" / "sessions" / "sess-output" / "playback-decisions.jsonl").read_text()
    assert "interrupt" in decisions
    assert any(event.event_name == "stream.output.cancelled" for event in connection.events)


def test_user_interrupt_cancels_current_output_stream(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    intent = OutputIntent(user_id="user-001", session_id="sess-interrupt", priority="normal")

    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-interrupt", text="playing", intent=intent)
    )
    app.publish_control_event(
        Event(
            event_name="control.user.interrupt.detected",
            user_id="user-001",
            producer_id="dev-playback",
            session_id="sess-interrupt",
            payload={"reason": "test_interrupt"},
        )
    )

    event_names = [event.event_name for event in connection.events]
    assert "stream.output.cancel.requested" in event_names
    assert "stream.output.cancelled" in event_names
    decisions = (tmp_path / "runs" / "sessions" / "sess-interrupt" / "playback-decisions.jsonl").read_text()
    assert "cancel_current" in decisions


def test_requeue_plays_after_interrupting_stream_finishes(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    low = OutputIntent(
        user_id="user-001",
        session_id="sess-low",
        priority="low",
        on_interrupted="requeue",
        on_blocked="drop",
        ttl_seconds=30,
    )
    high = OutputIntent(user_id="user-001", session_id="sess-high", priority="critical")

    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-low", text="low", intent=low)
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-high", text="high", intent=high)
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-high", text="", final=True, intent=high)
    )

    low_decisions = (tmp_path / "runs" / "sessions" / "sess-low" / "playback-decisions.jsonl").read_text()
    high_decisions = (tmp_path / "runs" / "sessions" / "sess-high" / "playback-decisions.jsonl").read_text()
    assert "play_now" in low_decisions
    assert "interrupt" in high_decisions
    assert len([event for event in connection.events if event.event_name == "stream.output.open.requested"]) >= 3


def test_queue_ttl_expiry_and_same_priority_no_interrupt(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    active = OutputIntent(user_id="user-001", session_id="sess-active", priority="normal")
    queued = OutputIntent(
        user_id="user-001",
        session_id="sess-queued",
        priority="normal",
        on_blocked="queue",
        ttl_seconds=-1,
    )

    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-active", text="active", intent=active)
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-queued", text="queued", intent=queued)
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-active", text="", final=True, intent=active)
    )

    queued_decisions = (tmp_path / "runs" / "sessions" / "sess-queued" / "playback-decisions.jsonl").read_text()
    assert "queue" in queued_decisions
    assert "ttl_expired" in queued_decisions
