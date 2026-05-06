from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, WSMsgType

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunk, StreamChunkCodec, StreamFormat, new_id


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


class NetworkPythonPlaybackEndpoint:
    """通过真实 HTTP/WebSocket 协议连接 server 的 Python playback endpoint。

    主要功能：模拟一台端侧设备，完成注册、唤醒、`sensor.mic` 上传、
    `actuator.speaker` 下行接收和播放回执。
    主要方法：`run_once()`。
    """

    def __init__(
        self,
        *,
        server_url: str,
        user_id: str,
        device_id: str,
        runs_root: str = "runs/audio-chat",
        auth: dict[str, Any] | None = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.user_id = user_id
        self.device_id = device_id
        self.runs_root = Path(runs_root)
        self.auth = auth or {"mode": "disabled"}
        self.sent_events: list[Event] = []
        self.received_events: list[Event] = []
        self.output_chunks: list[StreamChunk] = []
        self._started_output_streams: set[str] = set()
        self._session_id: str | None = None
        self._input_stream_id: str | None = None
        self._output_closed = asyncio.Event()
        self._session_closed = asyncio.Event()

    async def run_once(self, audio_payload: bytes | None = None) -> dict[str, Any]:
        """执行一次网络 playback 闭环。

        主要逻辑：建立控制和 stream WebSocket，注册设备，唤醒后按 server 请求打开
        `sensor.mic`，收到 speaker chunk 后上报 started / finished / closed。
        参数：`audio_payload` 为可选 PCM 输入。
        返回值：包含事件链、输出字节数和断言结果的 dict。
        异常情况：server 未启动、协议错误或断言超时会抛出异常。
        """
        async with ClientSession() as session:
            control_url = self.server_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws/control"
            stream_url = (
                self.server_url.replace("http://", "ws://").replace("https://", "wss://")
                + f"/ws/stream?device_id={self.device_id}"
            )
            async with session.ws_connect(control_url) as control_ws:
                await self._send_event(control_ws, self._registration_event())
                registered = await self._receive_event(control_ws)
                self.received_events.append(registered)
                if registered.event_name != "control.device.registered":
                    raise RuntimeError(f"device registration failed: {registered.payload}")
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="control.device.heartbeat.received",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        payload={"connection_id": registered.payload.get("connection_id")},
                    ),
                )
                async with session.ws_connect(stream_url) as stream_ws:
                    control_task = asyncio.create_task(self._control_loop(control_ws, stream_ws, audio_payload))
                    stream_task = asyncio.create_task(self._stream_loop(control_ws, stream_ws))
                    await self._send_event(
                        control_ws,
                        Event(
                            event_name="control.user.wake.detected",
                            user_id=self.user_id,
                            producer_id=self.device_id,
                            payload={"wake_source": "playback"},
                        ),
                    )
                    await asyncio.wait_for(self._output_closed.wait(), timeout=10)
                    await self._send_event(
                        control_ws,
                        Event(
                            event_name="control.user.dialog.close.requested",
                            user_id=self.user_id,
                            producer_id=self.device_id,
                            session_id=self._session_id,
                            payload={"reason": "playback_done"},
                        ),
                    )
                    await asyncio.wait_for(self._session_closed.wait(), timeout=10)
                    control_task.cancel()
                    stream_task.cancel()
        return self._build_result()

    async def _control_loop(self, control_ws, stream_ws, audio_payload: bytes | None) -> None:
        async for message in control_ws:
            if message.type != WSMsgType.TEXT:
                continue
            event = Event.from_dict(json.loads(message.data))
            self.received_events.append(event)
            if event.event_name == "control.audio_session.open.requested":
                self._session_id = event.session_id
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="control.audio_session.opened",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=event.session_id,
                        payload={"reason": "playback_opened"},
                    ),
                )
                await self._open_and_send_mic(control_ws, stream_ws, event.session_id, audio_payload)
            elif event.event_name == "stream.output.close.requested":
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.finished",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=event.session_id,
                        stream_id=event.stream_id,
                        stream_type=event.stream_type,
                        payload={"stream_type": event.stream_type},
                    ),
                )
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.closed",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=event.session_id,
                        stream_id=event.stream_id,
                        stream_type=event.stream_type,
                        payload={"stream_type": event.stream_type, "reason": "playback_closed"},
                    ),
                )
                self._output_closed.set()
            elif event.event_name == "control.audio_session.close.requested":
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="control.audio_session.closed",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=event.session_id,
                        payload={"reason": "playback_closed"},
                    ),
                )
                self._session_closed.set()

    async def _stream_loop(self, control_ws, stream_ws) -> None:
        async for message in stream_ws:
            if message.type != WSMsgType.BINARY:
                continue
            chunk = StreamChunkCodec.decode(message.data)
            if chunk.stream_id not in self._started_output_streams:
                self._started_output_streams.add(chunk.stream_id)
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.started",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=chunk.session_id,
                        stream_id=chunk.stream_id,
                        stream_type=chunk.stream_type,
                        payload={"stream_type": chunk.stream_type},
                    ),
                )
            self.output_chunks.append(chunk)

    async def _open_and_send_mic(self, control_ws, stream_ws, session_id: str | None, audio_payload: bytes | None) -> None:
        stream_id = new_id("stream_in")
        self._input_stream_id = stream_id
        stream_format = StreamFormat(codec="pcm16le", sample_rate=16000, channels=1, chunk_ms=20)
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.opened",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=session_id,
                stream_id=stream_id,
                stream_type="sensor.mic",
                payload={"stream_type": "sensor.mic", "format": stream_format.__dict__},
            ),
        )
        payload = audio_payload if audio_payload is not None else b"\x00\x00" * 320
        await stream_ws.send_bytes(
            StreamChunkCodec.encode(
                StreamChunk(
                    user_id=self.user_id,
                    session_id=session_id or "",
                    stream_id=stream_id,
                    stream_type="sensor.mic",
                    seq=0,
                    payload=payload,
                    final=True,
                    codec=stream_format.codec,
                    sample_rate=stream_format.sample_rate,
                    channels=stream_format.channels,
                    duration_ms=stream_format.chunk_ms,
                )
            )
        )
        # 控制和 stream 使用两条 WebSocket，端侧先给 server 一个极短处理窗口，避免
        # `stream.input.closed` 在二进制 chunk 前到达导致 server 提前关闭输入流。
        await asyncio.sleep(0.05)
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.closed",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=session_id,
                stream_id=stream_id,
                stream_type="sensor.mic",
                payload={"stream_type": "sensor.mic", "reason": "playback_input_done"},
            ),
        )

    async def _send_event(self, ws, event: Event) -> None:
        self.sent_events.append(event)
        await ws.send_str(json.dumps(event.to_dict(), ensure_ascii=False))

    async def _receive_event(self, ws) -> Event:
        message = await ws.receive(timeout=5)
        if message.type != WSMsgType.TEXT:
            raise RuntimeError(f"unexpected websocket message type: {message.type}")
        return Event.from_dict(json.loads(message.data))

    def _registration_event(self) -> Event:
        return Event(
            event_name="control.device.register.requested",
            user_id=self.user_id,
            producer_id=self.device_id,
            payload={
                "device_id": self.device_id,
                "device_name": "python-playback",
                "client_type": "python-playback",
                "sdk_version": "audio-chat-endpoint-0.1.0",
                "auth": self.auth,
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

    def _build_result(self) -> dict[str, Any]:
        event_names = [event.event_name for event in self.received_events + self.sent_events]
        if self._session_id:
            session_events_path = self.runs_root / "sessions" / self._session_id / "events.jsonl"
            if session_events_path.exists():
                for line in session_events_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        event_name = json.loads(line)["event_name"]
                        if event_name not in event_names:
                            event_names.append(event_name)
        result = {
            "user_id": self.user_id,
            "device_id": self.device_id,
            "session_id": self._session_id,
            "event_names": event_names,
            "endpoint_received_event_names": [event.event_name for event in self.received_events],
            "endpoint_sent_event_names": [event.event_name for event in self.sent_events],
            "output_chunk_count": len(self.output_chunks),
            "output_bytes": sum(len(chunk.payload) for chunk in self.output_chunks),
            "transport": "network",
        }
        result["assertions"] = {event_name: event_name in result["event_names"] for event_name in PLAYBACK_REQUIRED_EVENTS}
        result["passed"] = all(result["assertions"].values()) and result["output_chunk_count"] > 0
        if self._session_id:
            session_dir = self.runs_root / "sessions" / self._session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            (session_dir / "playback-result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
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


async def run_network_playback(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}
    endpoint = NetworkPythonPlaybackEndpoint(
        server_url=config.get("server_url", "http://127.0.0.1:8765"),
        user_id=config.get("user_id", "user-playback-001"),
        device_id=config.get("device_id", "dev-python-playback-001"),
        runs_root=config.get("runs_root", "runs/audio-chat"),
        auth=dict(config.get("auth") or {"mode": "disabled"}),
    )
    return await endpoint.run_once()


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
    if config.get("mode") == "in_process":
        result = run_playback(config)
    else:
        result = asyncio.run(run_network_playback(config))
    if not result.get("passed", False):
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
