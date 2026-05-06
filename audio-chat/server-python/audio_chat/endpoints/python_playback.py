from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunk


PLAYBACK_REQUIRED_EVENTS = (
    "control.device.registered",
    "control.audio_session.open.requested",
    "control.audio_session.opened",
    "stream.input.opened",
    "agent.response.started",
    "stream.output.open.requested",
    "stream.output.started",
    "stream.output.close.requested",
    "stream.output.finished",
    "stream.output.closed",
    "control.audio_session.close.requested",
    "control.audio_session.closed",
)


class PythonPlaybackEndpoint:
    def __init__(self, *, app: AudioChatApp, user_id: str, device_id: str) -> None:
        self.app = app
        self.user_id = user_id
        self.device_id = device_id
        self.events: list[Event] = []
        self.output_chunks: list[StreamChunk] = []
        self._started_output_streams: set[str] = set()

    def push_event(self, event: Event) -> None:
        self.events.append(event)
        if event.event_name == "control.audio_session.open.requested":
            self.app.publish_control_event(
                Event(
                    event_name="control.audio_session.opened",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=event.session_id,
                    payload={"reason": "playback_opened"},
                )
            )
        elif event.event_name == "stream.output.close.requested":
            self.app.publish_control_event(
                Event(
                    event_name="stream.output.finished",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=event.session_id,
                    stream_id=event.stream_id,
                    stream_type=event.stream_type,
                    payload={"stream_type": event.stream_type},
                )
            )
            self.app.publish_control_event(
                Event(
                    event_name="stream.output.closed",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=event.session_id,
                    stream_id=event.stream_id,
                    stream_type=event.stream_type,
                    payload={"stream_type": event.stream_type, "reason": "playback_closed"},
                )
            )
        elif event.event_name == "control.audio_session.close.requested":
            self.app.publish_control_event(
                Event(
                    event_name="control.audio_session.closed",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=event.session_id,
                    payload={"reason": "playback_closed"},
                )
            )
        elif event.event_name == "stream.control.configure.requested" and event.stream_type == "sensor.rgb":
            request_id = event.payload.get("request_id")
            handle = self.app.open_input_stream(
                user_id=self.user_id,
                producer_id=self.device_id,
                stream_type="sensor.rgb",
            )
            self.app.write_input_chunk(
                StreamChunk(
                    user_id=self.user_id,
                    session_id=handle.session_id,
                    stream_id=handle.stream_id,
                    stream_type="sensor.rgb",
                    seq=0,
                    payload=b"\xff\xd8mock-rgb\xff\xd9",
                    final=True,
                    metadata={"request_id": request_id} if request_id else {},
                )
            )
            self.app.stream_service.close_stream(handle.stream_id, reason="asset_uploaded")

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        if chunk.stream_id not in self._started_output_streams:
            self._started_output_streams.add(chunk.stream_id)
            self.app.publish_control_event(
                Event(
                    event_name="stream.output.started",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=chunk.session_id,
                    stream_id=chunk.stream_id,
                    stream_type=chunk.stream_type,
                    payload={"stream_type": chunk.stream_type},
                )
            )
        self.output_chunks.append(chunk)

    def run_once(self, audio_payload: bytes | None = None) -> dict[str, Any]:
        registration = Event(
            event_name="control.device.register.requested",
            user_id=self.user_id,
            producer_id=self.device_id,
            payload={
                "device_id": self.device_id,
                "device_name": "python-playback",
                "client_type": "python-playback",
                "sdk_version": "audio-chat-endpoint-0.1.0",
                "auth": {"mode": "disabled"},
                "capabilities": {
                    "streams.produce": ["sensor.mic", "sensor.rgb"],
                    "streams.consume": ["actuator.speaker"],
                    "audio.wake_word": "endpoint",
                    "audio.aec": "endpoint",
                    "sensor.rgb": True,
                },
                "subscriptions": [
                    {"event": "control.audio_session.*"},
                    {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
                    {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}},
                ],
            },
        )
        registered = self.app.register_device(registration, self)
        self.events.append(registered)
        self.app.publish_control_event(
            Event(
                event_name="control.user.wake.detected",
                user_id=self.user_id,
                producer_id=self.device_id,
                payload={"wake_source": "playback"},
            )
        )
        handle = self.app.open_input_stream(user_id=self.user_id, producer_id=self.device_id)
        payload = audio_payload if audio_payload is not None else b"\x00\x00" * 320
        self.app.write_input_chunk(
            StreamChunk(
                user_id=self.user_id,
                session_id=handle.session_id,
                stream_id=handle.stream_id,
                stream_type="sensor.mic",
                seq=0,
                payload=payload,
                final=True,
            )
        )
        self.app.stream_service.close_stream(handle.stream_id, reason="playback_input_done")
        self.app.close_audio_session(self.user_id, reason="mock_response_completed")
        session_events_path = self.app.recorder.session_dir(handle.session_id) / "events.jsonl"
        session_event_names = [
            json.loads(line)["event_name"]
            for line in session_events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        event_names = [event.event_name for event in self.events] + [
            event_name for event_name in session_event_names if event_name not in {event.event_name for event in self.events}
        ]
        result = {
            "user_id": self.user_id,
            "device_id": self.device_id,
            "session_id": handle.session_id,
            "event_names": event_names,
            "endpoint_received_event_names": [event.event_name for event in self.events],
            "output_chunk_count": len(self.output_chunks),
            "output_bytes": sum(len(chunk.payload) for chunk in self.output_chunks),
        }
        result["assertions"] = {
            event_name: event_name in result["event_names"]
            for event_name in PLAYBACK_REQUIRED_EVENTS
        }
        result["passed"] = all(result["assertions"].values()) and result["output_chunk_count"] > 0
        self.app.recorder.record_playback_result(handle.session_id, result)
        return result


def run_playback(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}
    app = AudioChatApp(
        AudioChatConfig(
            runs_root=config.get("runs_root", "runs/audio-chat"),
            asr_provider=config.get("asr_provider", "mock"),
            asr_model=config.get("asr_model", "mock-asr"),
            text_model_provider=config.get("text_model_provider", "mock"),
            text_model=config.get("text_model", "mock-text"),
            tts_provider=config.get("tts_provider", "mock"),
            tts_model=config.get("tts_model", "mock-tts"),
            tts_voice=config.get("tts_voice", "mock"),
        )
    )
    endpoint = PythonPlaybackEndpoint(
        app=app,
        user_id=config.get("user_id", "user-playback-001"),
        device_id=config.get("device_id", "dev-python-playback-001"),
    )
    return endpoint.run_once()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="")
    args = parser.parse_args(argv)
    config: dict[str, Any] = {}
    if args.config:
        text = Path(args.config).read_text(encoding="utf-8")
        if text.strip().startswith("{"):
            config = json.loads(text)
        else:
            import yaml

            config = yaml.safe_load(text) or {}
    result = run_playback(config)
    if not result.get("passed", False):
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
