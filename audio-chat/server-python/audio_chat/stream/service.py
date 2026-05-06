from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from audio_chat.control import ControlService
from audio_chat.observability import RunRecorder
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamChunk, StreamFormat, new_id


class StreamDispatcher(Protocol):
    def dispatch(self, chunk: StreamChunk) -> None: ...


@dataclass
class StreamHandle:
    user_id: str
    session_id: str
    stream_id: str
    stream_type: str
    producer_id: str
    format: StreamFormat
    state: str = "open"
    consumer_device_ids: tuple[str, ...] = ()


class StreamRegistry:
    def __init__(self) -> None:
        self._streams: dict[str, StreamHandle] = {}

    def register(self, handle: StreamHandle) -> None:
        self._streams[handle.stream_id] = handle

    def get(self, stream_id: str) -> StreamHandle:
        return self._streams[stream_id]

    def has(self, stream_id: str) -> bool:
        return stream_id in self._streams

    def list_by_user(self, user_id: str) -> list[StreamHandle]:
        return [handle for handle in self._streams.values() if handle.user_id == user_id]


class StreamService:
    def __init__(
        self,
        *,
        control_service: ControlService,
        dispatcher: StreamDispatcher | None = None,
        recorder: RunRecorder | None = None,
        max_chunk_bytes: int = 8192,
        default_sensor_mic: StreamFormat | None = None,
        default_actuator_speaker: StreamFormat | None = None,
    ) -> None:
        self.control_service = control_service
        self.dispatcher = dispatcher
        self.recorder = recorder or control_service.recorder
        self.registry = StreamRegistry()
        self.max_chunk_bytes = max_chunk_bytes
        self.default_sensor_mic = default_sensor_mic or StreamFormat()
        self.default_actuator_speaker = default_actuator_speaker or StreamFormat(chunk_ms=40)

    def set_dispatcher(self, dispatcher: StreamDispatcher) -> None:
        self.dispatcher = dispatcher

    def open_stream(
        self,
        *,
        user_id: str,
        session_id: str,
        stream_type: str,
        producer_id: str,
        format: StreamFormat | None = None,
        stream_id: str | None = None,
    ) -> StreamHandle:
        stream_id = stream_id or new_id("stream")
        stream_format = format or self.default_format_for(stream_type)
        self._validate_stream(stream_type=stream_type, format=stream_format)
        consumers: tuple[str, ...] = ()
        handle = StreamHandle(
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            stream_type=stream_type,
            producer_id=producer_id,
            format=stream_format,
        )
        self.registry.register(handle)
        if stream_type.startswith("actuator."):
            event = Event(
                event_name="stream.output.open.requested",
                user_id=user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=session_id,
                stream_id=stream_id,
                stream_type=stream_type,
                payload={
                    "stream_type": stream_type,
                    "format": stream_format.__dict__,
                },
            )
            consumers = tuple(device.device_id for device in self.control_service.resolve_subscribers(event))
            handle.consumer_device_ids = consumers
            self.control_service.publish(event)
        self.recorder.record_stream_event(
            session_id,
            {
                "event": "stream.opened",
                "stream_id": stream_id,
                "stream_type": stream_type,
                "producer_id": producer_id,
                "consumer_device_ids": list(consumers),
            },
        )
        return handle

    def on_chunk(self, chunk: StreamChunk) -> None:
        handle = self.registry.get(chunk.stream_id)
        self._validate_chunk(chunk, handle=handle)
        if handle.state != "open":
            raise ValueError("stream is not open")
        self.recorder.record_stream_payload(chunk)
        self.recorder.record_stream_event(
            chunk.session_id,
            {
                "event": "stream.chunk.received",
                "stream_id": chunk.stream_id,
                "stream_type": chunk.stream_type,
                "seq": chunk.seq,
                "payload_size": len(chunk.payload),
                "final": chunk.final,
            },
        )
        if self.dispatcher is not None:
            self.dispatcher.dispatch(chunk)

    def write_chunk(self, chunk: StreamChunk) -> None:
        handle = self.registry.get(chunk.stream_id)
        self._validate_chunk(chunk, handle=handle)
        if handle.state != "open":
            raise ValueError("stream is not open")
        self.recorder.record_stream_payload(chunk)
        self.recorder.record_stream_event(
            chunk.session_id,
            {
                "event": "stream.chunk.sent",
                "stream_id": chunk.stream_id,
                "stream_type": chunk.stream_type,
                "seq": chunk.seq,
                "payload_size": len(chunk.payload),
                "final": chunk.final,
            },
        )
        self.control_service.push_stream_chunk_to_devices(handle.consumer_device_ids, chunk)

    def close_stream(self, stream_id: str, *, reason: str = "completed") -> None:
        handle = self.registry.get(stream_id)
        handle.state = "closed"
        event_name = "stream.output.close.requested" if handle.stream_type.startswith("actuator.") else "stream.input.closed"
        event = Event(
            event_name=event_name,
            user_id=handle.user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type=handle.stream_type,
            payload={"stream_type": handle.stream_type, "reason": reason},
        )
        self.control_service.publish(event)
        self.recorder.record_stream_event(
            handle.session_id,
            {
                "event": "stream.closed",
                "stream_id": handle.stream_id,
                "stream_type": handle.stream_type,
                "reason": reason,
            },
        )

    def cancel_stream(self, stream_id: str, *, reason: str) -> None:
        handle = self.registry.get(stream_id)
        handle.state = "cancelled"
        request = Event(
            event_name="stream.output.cancel.requested",
            user_id=handle.user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type=handle.stream_type,
            payload={"stream_type": handle.stream_type, "reason": reason},
        )
        self.control_service.publish(request)
        event = Event(
            event_name="stream.output.cancelled",
            user_id=handle.user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type=handle.stream_type,
            payload={"stream_type": handle.stream_type, "reason": reason},
        )
        self.control_service.publish(event)
        self.recorder.record_stream_event(
            handle.session_id,
            {
                "event": "stream.closed",
                "stream_id": handle.stream_id,
                "stream_type": handle.stream_type,
                "reason": reason,
                "state": "cancelled",
            },
        )

    def default_format_for(self, stream_type: str) -> StreamFormat:
        if stream_type == "sensor.mic":
            return self.default_sensor_mic
        if stream_type == "actuator.speaker":
            return self.default_actuator_speaker
        return StreamFormat()

    def _validate_stream(self, *, stream_type: str, format: StreamFormat) -> None:
        if stream_type not in {"sensor.mic", "sensor.rgb", "sensor.depth", "sensor.imu", "actuator.speaker", "actuator.haptic"}:
            raise ValueError(f"unknown stream_type: {stream_type}")
        if format.codec not in {"pcm16le", "jpeg", "raw"}:
            raise ValueError(f"unsupported codec: {format.codec}")
        if format.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if format.channels <= 0:
            raise ValueError("channels must be positive")

    def _validate_chunk(self, chunk: StreamChunk, *, handle: StreamHandle) -> None:
        if len(chunk.payload) > self.max_chunk_bytes:
            raise ValueError("chunk exceeds stream.max_chunk_bytes")
        if chunk.stream_type != handle.stream_type:
            raise ValueError("chunk stream_type does not match stream handle")
        if chunk.codec != handle.format.codec:
            raise ValueError("chunk codec does not match stream format")
        if chunk.sample_rate != handle.format.sample_rate:
            raise ValueError("chunk sample_rate does not match stream format")
        if chunk.channels != handle.format.channels:
            raise ValueError("chunk channels does not match stream format")
