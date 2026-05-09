from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from audio_chat.control import ControlService
from audio_chat.observability import RunRecorder
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamChunk, StreamFormat, new_id


class StreamDispatcher(Protocol):
    def dispatch(self, chunk: StreamChunk) -> None: ...


class StreamNotOpenError(ValueError):
    """Raised when a chunk arrives for a stream that has already stopped."""

    def __init__(self, handle: "StreamHandle") -> None:
        super().__init__(f"stream is not open: state={handle.state}")
        self.stream_id = handle.stream_id
        self.stream_type = handle.stream_type
        self.session_id = handle.session_id
        self.user_id = handle.user_id
        self.state = handle.state


@dataclass
class StreamHandle:
    """Stream 运行时句柄。

    主要功能：记录一条输入或输出 stream 的生命周期、格式和冻结消费者。
    主要属性：`consumer_device_ids` 在需要转发的 stream 打开时计算一次，后续 chunk、
    close 和 cancel 都复用这组冻结消费者，避免插播或设备重连改变旧 stream 语义。
    """

    user_id: str
    session_id: str
    stream_id: str
    stream_type: str
    producer_id: str
    format: StreamFormat
    state: str = "open"
    consumer_device_ids: tuple[str, ...] = ()
    opened_at: float = 0.0
    last_activity_at: float = 0.0

    def __post_init__(self) -> None:
        """初始化生命周期时间戳。"""

        now = time.time()
        if not self.opened_at:
            self.opened_at = now
        if not self.last_activity_at:
            self.last_activity_at = now

    def touch(self) -> None:
        """刷新 stream 最近活跃时间。"""

        self.last_activity_at = time.time()


class StreamRegistry:
    def __init__(self) -> None:
        self._streams: dict[str, StreamHandle] = {}

    def register(self, handle: StreamHandle) -> None:
        self._streams[handle.stream_id] = handle

    def get(self, stream_id: str) -> StreamHandle:
        """按 stream_id 读取已注册 stream。

        主要逻辑：把内部字典的 `KeyError` 转成可读协议错误，方便 WebSocket 端返回
        明确的 unknown stream 诊断。
        参数：`stream_id` 为端侧或服务端声明的 stream 标识。
        返回值：对应 `StreamHandle`。
        异常情况：stream 未注册时抛出 `ValueError`。
        """
        if stream_id not in self._streams:
            raise ValueError(f"unknown stream_id: {stream_id}")
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
        max_chunk_bytes: int = 1048576,
        idle_timeout_seconds: float = 20.0,
        default_sensor_mic: StreamFormat | None = None,
        default_actuator_speaker: StreamFormat | None = None,
    ) -> None:
        self.control_service = control_service
        self.dispatcher = dispatcher
        self.recorder = recorder or control_service.recorder
        self.registry = StreamRegistry()
        self.max_chunk_bytes = max_chunk_bytes
        self.idle_timeout_seconds = idle_timeout_seconds
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
        selection: str = "all",
        consumer_device_ids: tuple[str, ...] | None = None,
    ) -> StreamHandle:
        """打开输入或输出 stream。

        主要逻辑：输入 stream 注册本地句柄；非麦克风传感器输入 stream 会按
        `stream.input.opened` 订阅冻结消费者，用于把眼镜 RGB 等上行数据转发给手机
        端侧；输出 stream 先按订阅和 selection 选出 consumer，再发布
        `stream.output.open.requested`，后续 chunk 和 close/cancel 事件都只发送给这批
        consumer。
        参数：`selection` 控制多个订阅命中设备时的选择策略。
        返回值：`StreamHandle`。
        异常情况：stream 类型、格式或 selection 非法时抛出 `ValueError`。
        """
        stream_id = stream_id or new_id("stream")
        stream_format = format or self.default_format_for(stream_type)
        self._validate_stream(stream_type=stream_type, format=stream_format)
        consumers: tuple[str, ...] = tuple(consumer_device_ids or ())
        handle = StreamHandle(
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            stream_type=stream_type,
            producer_id=producer_id,
            format=stream_format,
        )
        self.registry.register(handle)
        if stream_type.startswith("sensor.") and stream_type != "sensor.mic":
            match_event = Event(
                event_name="stream.input.opened",
                user_id=user_id,
                producer_id=producer_id,
                session_id=session_id,
                stream_id=stream_id,
                stream_type=stream_type,
                payload={
                    "stream_type": stream_type,
                    "format": stream_format.__dict__,
                },
            )
            matched = self.control_service.resolve_matching_devices(
                match_event,
                selection=selection,
            )
            consumers = tuple(device.device_id for device in matched)
            handle.consumer_device_ids = consumers
        if stream_type.startswith("actuator."):
            match_event = Event(
                event_name="stream.output.open.requested",
                user_id=user_id,
                producer_id=SERVER_PRODUCER_ID,
                stream_id=stream_id,
                stream_type=stream_type,
                payload={
                    "stream_type": stream_type,
                    "format": stream_format.__dict__,
                },
            )
            if consumer_device_ids is None:
                matched = self.control_service.resolve_matching_devices(
                    match_event,
                    selection=selection,
                )
                consumers = tuple(device.device_id for device in matched)
            if len(consumers) == 1:
                handle.session_id = consumers[0]
            event = Event(
                event_name=match_event.event_name,
                user_id=match_event.user_id,
                producer_id=match_event.producer_id,
                session_id=handle.session_id,
                stream_id=match_event.stream_id,
                stream_type=match_event.stream_type,
                payload=match_event.payload,
            )
            handle.consumer_device_ids = consumers
            self.control_service._push_event_to_device_ids(event, consumers)
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
            raise StreamNotOpenError(handle)
        handle.touch()
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
        if handle.consumer_device_ids:
            self.control_service.push_stream_chunk_to_devices(handle.consumer_device_ids, chunk)
        if self.dispatcher is not None:
            self.dispatcher.dispatch(chunk)

    def write_chunk(self, chunk: StreamChunk) -> None:
        handle = self.registry.get(chunk.stream_id)
        self._validate_chunk(chunk, handle=handle)
        if handle.state != "open":
            raise StreamNotOpenError(handle)
        handle.touch()
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
        if handle.state == "closed":
            return
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
        if handle.stream_type.startswith("actuator."):
            self.control_service._push_event_to_device_ids(event, handle.consumer_device_ids)
        else:
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
        if handle.state == "cancelled":
            return
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
        self.control_service._push_event_to_device_ids(request, handle.consumer_device_ids)
        event = Event(
            event_name="stream.output.cancelled",
            user_id=handle.user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type=handle.stream_type,
            payload={"stream_type": handle.stream_type, "reason": reason},
        )
        self.control_service._push_event_to_device_ids(event, handle.consumer_device_ids)
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

    def fail_stream(self, stream_id: str, *, reason: str) -> None:
        """标记 stream 失败并发布对应生命周期事件。

        主要逻辑：输入 stream 发布 `stream.input.failed`；输出 stream 按冻结消费者推送
        `stream.output.failed`，不重新按订阅匹配设备。
        参数：`stream_id` 为目标 stream，`reason` 为失败原因。
        返回值：无。
        异常情况：stream 不存在时由 registry 抛出 `ValueError`。
        """

        handle = self.registry.get(stream_id)
        if handle.state == "failed":
            return
        handle.state = "failed"
        event_name = "stream.output.failed" if handle.stream_type.startswith("actuator.") else "stream.input.failed"
        event = Event(
            event_name=event_name,
            user_id=handle.user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type=handle.stream_type,
            payload={"stream_type": handle.stream_type, "reason": reason},
        )
        if handle.stream_type.startswith("actuator."):
            self.control_service._push_event_to_device_ids(event, handle.consumer_device_ids)
        else:
            self.control_service.publish(event)
        self.recorder.record_stream_event(
            handle.session_id,
            {
                "event": "stream.failed",
                "stream_id": handle.stream_id,
                "stream_type": handle.stream_type,
                "reason": reason,
            },
        )

    def close_idle_streams(self, *, now: float | None = None) -> list[StreamHandle]:
        """关闭超过空闲阈值的 stream。

        主要逻辑：扫描所有 open stream；超过 `idle_timeout_seconds` 时按普通关闭流程
        发布生命周期事件，并返回被关闭的句柄列表，便于上层 sweeper 或测试确认结果。
        参数：`now` 为可选当前时间，测试可传入固定时间。
        返回值：被关闭的 `StreamHandle` 列表。
        异常情况：无。
        """

        if self.idle_timeout_seconds <= 0:
            return []
        current = now if now is not None else time.time()
        closed: list[StreamHandle] = []
        for handle in list(self.registry._streams.values()):
            if handle.state != "open":
                continue
            if current - handle.last_activity_at < self.idle_timeout_seconds:
                continue
            self.close_stream(handle.stream_id, reason="idle_timeout")
            closed.append(handle)
        return closed

    def default_format_for(self, stream_type: str) -> StreamFormat:
        if stream_type == "sensor.mic":
            return self.default_sensor_mic
        if stream_type == "actuator.speaker":
            return self.default_actuator_speaker
        return StreamFormat()

    def _validate_stream(self, *, stream_type: str, format: StreamFormat) -> None:
        if stream_type not in {
            "sensor.mic",
            "sensor.rgb",
            "sensor.depth",
            "sensor.tof",
            "sensor.imu",
            "actuator.speaker",
            "actuator.haptic",
        }:
            raise ValueError(f"unknown stream_type: {stream_type}")
        if format.codec not in {"pcm16le", "jpeg", "png", "raw"}:
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
