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

    def list_by_user(self, user_id: str) -> list[StreamHandle]:
        return [handle for handle in self._streams.values() if handle.user_id == user_id]


class StreamService:
    def __init__(
        self,
        *,
        control_service: ControlService,
        dispatcher: StreamDispatcher | None = None,
        recorder: RunRecorder | None = None,
    ) -> None:
        self.control_service = control_service
        self.dispatcher = dispatcher
        self.recorder = recorder or control_service.recorder
        self.registry = StreamRegistry()

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
        stream_format = format or StreamFormat()
        consumers: tuple[str, ...] = ()
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
            self.control_service.publish(event)
        handle = StreamHandle(
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            stream_type=stream_type,
            producer_id=producer_id,
            format=stream_format,
            consumer_device_ids=consumers,
        )
        self.registry.register(handle)
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
        if handle.state != "open":
            raise ValueError("stream is not open")
        if chunk.stream_type != handle.stream_type:
            raise ValueError("chunk stream_type does not match stream handle")
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
        for device_id in handle.consumer_device_ids:
            connection = self.control_service._connections.get(device_id)
            if connection is not None:
                connection.push_stream_chunk(chunk)

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
