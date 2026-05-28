from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from realtime_agent.control import ControlService
from realtime_agent.observability import RunRecorder
from realtime_agent.protocol import SERVER_PRODUCER_ID, Event, StreamChunk, StreamFormat, new_id


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
    close 和 cancel 都复用这组冻结消费者，避免插播或设备重连改变已打开 stream 的消费者集合。
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
    output_ready: bool = False
    pending_output_chunks: list[StreamChunk] = field(default_factory=list)
    pending_output_finish_event: Event | None = None

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
        consumer。输出 stream 的首包会等端侧回 `stream.output.ready` 后再下发。
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
        if stream_type.startswith("sensor.") and stream_type != "sensor.mic" and consumer_device_ids is None:
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
        if stream_type.startswith("sensor.") and stream_type != "sensor.mic":
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
            allow_late_sensor_chunk = False
            if handle.state == "closed" and handle.stream_type.startswith("sensor."):
                is_final_asset_chunk = handle.stream_type != "sensor.mic" and chunk.final
                request_id = chunk.metadata.get("request_id")
                allow_late_sensor_chunk = bool(request_id or is_final_asset_chunk)
                if allow_late_sensor_chunk:
                    reason = (
                        "request_asset_control_close_race"
                        if request_id
                        else "input_stream_closed_final_chunk_race"
                    )
                    event = {
                        "event": "stream.chunk.received_after_close",
                        "stream_id": chunk.stream_id,
                        "stream_type": chunk.stream_type,
                        "seq": chunk.seq,
                        "payload_size": len(chunk.payload),
                        "reason": reason,
                    }
                    if request_id:
                        event["request_id"] = request_id
                    self.recorder.record_stream_event(chunk.session_id, event)
                else:
                    self.recorder.record_stream_event(
                        chunk.session_id,
                        {
                            "event": "stream.chunk.dropped",
                            "stream_id": chunk.stream_id,
                            "stream_type": chunk.stream_type,
                            "seq": chunk.seq,
                            "payload_size": len(chunk.payload),
                            "reason": "input_stream_closed_late_chunk",
                        },
                    )
                    return
            if not allow_late_sensor_chunk:
                raise StreamNotOpenError(handle)
        handle.touch()
        self.recorder.record_stream_payload(chunk)
        self.recorder.record_stream_event(
            chunk.session_id,
            {
                "event": "stream.chunk.received",
                "user_id": chunk.user_id,
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
        if handle.stream_type.startswith("actuator.") and not handle.output_ready:
            handle.pending_output_chunks.append(chunk)
            self.recorder.record_stream_event(
                chunk.session_id,
                {
                    "event": "stream.output.chunk_buffered_until_ready",
                    "user_id": chunk.user_id,
                    "stream_id": chunk.stream_id,
                    "stream_type": chunk.stream_type,
                    "seq": chunk.seq,
                    "payload_size": len(chunk.payload),
                    "pending_chunks": len(handle.pending_output_chunks),
                },
            )
            return
        self._send_output_chunk(handle, chunk)

    def _send_output_chunk(self, handle: StreamHandle, chunk: StreamChunk) -> None:
        """记录并发送一片 output chunk。

        主要逻辑：output ready 前由 `write_chunk()` 暂存；ready 后通过本方法统一记录
        和下发，保证首包不会早于端侧本轮逻辑 output stream 状态重置完成。
        参数：`handle` 为输出 stream 句柄，`chunk` 为要下发的字节分片。
        返回值：无。
        异常情况：无。
        """

        self.recorder.record_stream_payload(chunk)
        self.recorder.record_stream_event(
            chunk.session_id,
            {
                "event": "stream.chunk.sent",
                "user_id": chunk.user_id,
                "stream_id": chunk.stream_id,
                "stream_type": chunk.stream_type,
                "seq": chunk.seq,
                "payload_size": len(chunk.payload),
                "final": chunk.final,
            },
        )
        self.control_service.push_stream_chunk_to_devices(handle.consumer_device_ids, chunk)

    def mark_output_endpoint_ready(self, stream_id: str, *, reason: str = "endpoint_ready") -> None:
        """记录端侧已经准备好接收本轮 output stream，并冲刷暂存分片。

        主要逻辑：端侧回 `stream.output.ready` 后，server 才能向已建立的音频下行链路
        写入本轮 speaker chunk。若上层在 ready 前已经写入 chunk 或请求 finish，这里按
        原顺序先冲刷 chunk，再发送 finish/close 控制事件。
        参数：`stream_id` 为 output stream 标识，`reason` 为回执来源。
        返回值：无。
        异常情况：stream 不存在或不是 output stream 时抛出 `ValueError`。
        """

        handle = self.registry.get(stream_id)
        if not handle.stream_type.startswith("actuator."):
            raise ValueError(f"not an output stream: {stream_id}")
        if handle.output_ready:
            return
        handle.output_ready = True
        handle.touch()
        pending_chunks = list(handle.pending_output_chunks)
        handle.pending_output_chunks.clear()
        self.recorder.record_stream_event(
            handle.session_id,
            {
                "event": "stream.output.endpoint_ready",
                "stream_id": handle.stream_id,
                "stream_type": handle.stream_type,
                "reason": reason,
                "pending_chunks": len(pending_chunks),
                "state": handle.state,
            },
        )
        for chunk in pending_chunks:
            self._send_output_chunk(handle, chunk)
        pending_finish = handle.pending_output_finish_event
        if pending_finish is not None:
            handle.pending_output_finish_event = None
            self.control_service._push_event_to_device_ids(pending_finish, handle.consumer_device_ids)

    def close_stream(self, stream_id: str, *, reason: str = "completed") -> None:
        handle = self.registry.get(stream_id)
        if handle.state == "closed":
            return
        handle.state = "closed"
        event_name = (
            "stream.output.finish.requested"
            if handle.stream_type == "actuator.speaker"
            else ("stream.output.close.requested" if handle.stream_type.startswith("actuator.") else "stream.input.closed")
        )
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
            if handle.output_ready:
                self.control_service._push_event_to_device_ids(event, handle.consumer_device_ids)
            else:
                handle.pending_output_finish_event = event
                self.recorder.record_stream_event(
                    handle.session_id,
                    {
                        "event": "stream.output.close_buffered_until_ready",
                        "stream_id": handle.stream_id,
                        "stream_type": handle.stream_type,
                        "reason": reason,
                        "state": handle.state,
                    },
                )
        elif handle.stream_type == "sensor.mic":
            # 麦克风输入流的生产端必须收到关闭通知，否则浏览器会继续复用已关闭的 stream_id。
            self.control_service._push_event_to_device_ids(event, (handle.producer_id,), route_reason="stream_producer")
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

    def request_output_finish(
        self,
        stream_id: str,
        *,
        reason: str = "completed",
        output_bytes: int | None = None,
        output_chunk_count: int | None = None,
        output_last_seq: int | None = None,
    ) -> None:
        """通知端侧 output stream 已写完，但不关闭服务端 stream 句柄。

        主要逻辑：`stream.output.finish.requested` 只表示 server 已经没有更多
        `StreamChunk` 要写入；端侧还需要把 SDK buffer 和本地 speaker sink drain
        完成后再回 `stream.output.closed`。因此这里把状态标记为
        `finish_requested`，由 `mark_output_endpoint_closed()` 最终收口。
        参数：`stream_id` 为 output stream 标识，`reason` 为写完原因；
        `output_bytes`、`output_chunk_count`、`output_last_seq` 用于跨 WebSocket
        场景下让端侧判断 finish 前的最后一帧是否已经进入播放队列。
        返回值：无。
        异常情况：stream 不存在或不是 output stream 时抛出 `ValueError`。
        """

        handle = self.registry.get(stream_id)
        if not handle.stream_type.startswith("actuator."):
            raise ValueError(f"not an output stream: {stream_id}")
        if handle.state in {"finish_requested", "closed", "cancel_requested", "cancelled", "failed"}:
            return
        handle.state = "finish_requested"
        event_name = "stream.output.finish.requested" if handle.stream_type == "actuator.speaker" else "stream.output.close.requested"
        payload = {"stream_type": handle.stream_type, "reason": reason}
        if output_bytes is not None:
            payload["output_bytes"] = output_bytes
        if output_chunk_count is not None:
            payload["output_chunk_count"] = output_chunk_count
        if output_last_seq is not None:
            payload["output_last_seq"] = output_last_seq
        event = Event(
            event_name=event_name,
            user_id=handle.user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type=handle.stream_type,
            payload=payload,
        )
        if handle.output_ready:
            self.control_service._push_event_to_device_ids(event, handle.consumer_device_ids)
        else:
            handle.pending_output_finish_event = event
            self.recorder.record_stream_event(
                handle.session_id,
                {
                    "event": "stream.output.finish_buffered_until_ready",
                    "stream_id": handle.stream_id,
                    "stream_type": handle.stream_type,
                    "reason": reason,
                    "state": handle.state,
                },
            )
        record = {
            "event": "stream.output.finish_requested",
            "stream_id": handle.stream_id,
            "stream_type": handle.stream_type,
            "reason": reason,
            "state": handle.state,
        }
        if output_bytes is not None:
            record["output_bytes"] = output_bytes
        if output_chunk_count is not None:
            record["output_chunk_count"] = output_chunk_count
        if output_last_seq is not None:
            record["output_last_seq"] = output_last_seq
        self.recorder.record_stream_event(handle.session_id, record)

    def mark_output_endpoint_started(self, stream_id: str, *, reason: str = "endpoint_started") -> None:
        """记录端侧已经开始播放 output stream。"""

        handle = self.registry.get(stream_id)
        if not handle.stream_type.startswith("actuator."):
            raise ValueError(f"not an output stream: {stream_id}")
        self.recorder.record_stream_event(
            handle.session_id,
            {
                "event": "stream.output.endpoint_started",
                "stream_id": handle.stream_id,
                "stream_type": handle.stream_type,
                "reason": reason,
                "state": handle.state,
            },
        )

    def mark_output_endpoint_closed(self, stream_id: str, *, reason: str = "endpoint_closed", state: str = "closed") -> None:
        """记录端侧 output stream 终态并关闭服务端句柄。

        主要逻辑：只有端侧回 `stream.output.closed/cancelled/failed` 后，server
        才把 output stream 从 `finish_requested/cancel_requested/open` 进入终态。
        """

        handle = self.registry.get(stream_id)
        if not handle.stream_type.startswith("actuator."):
            raise ValueError(f"not an output stream: {stream_id}")
        if handle.state in {"closed", "cancelled", "failed"}:
            return
        handle.state = state
        self.recorder.record_stream_event(
            handle.session_id,
            {
                "event": "stream.output.endpoint_closed",
                "stream_id": handle.stream_id,
                "stream_type": handle.stream_type,
                "reason": reason,
                "state": state,
            },
        )
        self.recorder.record_stream_event(
            handle.session_id,
            {
                "event": "stream.closed",
                "stream_id": handle.stream_id,
                "stream_type": handle.stream_type,
                "reason": reason,
                "state": state,
            },
        )

    def cancel_stream(self, stream_id: str, *, reason: str) -> None:
        handle = self.registry.get(stream_id)
        if handle.state in {"cancel_requested", "cancelled", "closed", "failed"}:
            return
        handle.state = "cancel_requested"
        handle.pending_output_chunks.clear()
        handle.pending_output_finish_event = None
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
        self.recorder.record_stream_event(
            handle.session_id,
            {
                "event": "stream.output.cancel_requested",
                "stream_id": handle.stream_id,
                "stream_type": handle.stream_type,
                "reason": reason,
                "state": handle.state,
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
        handle.pending_output_chunks.clear()
        handle.pending_output_finish_event = None
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
