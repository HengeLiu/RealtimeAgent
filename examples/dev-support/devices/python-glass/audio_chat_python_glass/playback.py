from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import ClientSession

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat_device import AudioChatDeviceClient, AudioChatEvent as Event, StreamChunk as DeviceStreamChunk, ws_url


@dataclass(frozen=True)
class PlaybackAudio:
    """回放音频描述。

    主要功能：为旧测试和端侧 mock 提供轻量音频元数据结构。
    主要属性：`source_path` 为 WAV 路径，`payload` 为 PCM 字节。
    """

    source_path: str
    payload: bytes
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2
    chunk_ms: int = 20

    @property
    def total_bytes(self) -> int:
        """返回 PCM 总字节数。"""

        return len(self.payload)

    @property
    def chunk_count(self) -> int:
        """返回按 chunk_ms 估算的分片数。"""

        chunk_bytes = max(1, int(self.sample_rate * self.chunk_ms / 1000 * self.channels * self.sample_width))
        return max(1, (len(self.payload) + chunk_bytes - 1) // chunk_bytes)


def load_wav_audio(path: str | Path) -> PlaybackAudio:
    """读取 WAV 样例为 PlaybackAudio。

    参数：`path` 为 WAV 文件路径。
    返回值：`PlaybackAudio`。
    异常情况：文件不存在或 WAV 损坏时由 wave 模块抛出异常。
    """

    import wave

    source_path = _resolve_audio_sample_path(path)
    with wave.open(str(source_path), "rb") as wav_file:
        payload = wav_file.readframes(wav_file.getnframes())
        return PlaybackAudio(
            source_path=str(source_path),
            payload=payload,
            sample_rate=wav_file.getframerate(),
            channels=wav_file.getnchannels(),
            sample_width=wav_file.getsampwidth(),
        )


class NetworkPythonPlaybackEndpoint:
    """Python 参考端网络基类。

    主要功能：提供注册、控制事件发送和基础运行摘要，供 Python phone mock 复用。
    当前仓库没有完整 python-glass 目录时，本类保证 phone 参考端可独立运行。
    """

    def __init__(
        self,
        *,
        server_url: str,
        user_id: str,
        device_id: str,
        runs_root: str = "runs/audio-chat",
        auth: dict[str, Any] | None = None,
        device_name: str = "python-glass",
        client_type: str = "python-glass",
        properties: dict[str, Any] | None = None,
        supports: dict[str, Any] | None = None,
        rgb_payload: bytes | None = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.user_id = user_id
        self.device_id = device_id
        self.runs_root = runs_root
        self.auth = dict(auth or {"mode": "disabled"})
        self.device_name = device_name
        self.client_type = client_type
        if properties is None:
            self.properties = {
                "audio_chat.audio_input": "sensor.mic",
                "audio_chat.audio_output": "actuator.speaker",
            }
        else:
            self.properties = dict(properties)
            if "speaker.role" in self.properties and "audio_chat.audio_output" not in self.properties:
                self.properties["audio_chat.audio_output"] = "actuator.speaker"
            if "microphone.role" in self.properties and "audio_chat.audio_input" not in self.properties:
                self.properties["audio_chat.audio_input"] = "sensor.mic"
        self.supports = dict(supports or {"sensors": [{"type": "rgb", "modes": ["single"]}], "actuators": []})
        self.rgb_payload = rgb_payload or b"\xff\xd8mock-rgb\xff\xd9"
        self.client = AudioChatDeviceClient(
            server_url=self.server_url,
            device={
                "user_id": self.user_id,
                "device_id": self.device_id,
                "device_name": self.device_name,
                "name": self.device_name,
                "client_type": self.client_type,
                "sdk_version": "audio-chat-python-glass-compat-0.1.0",
                "auth": self.auth,
                "supports": self.supports,
                "properties": self.properties,
            },
        )
        self.received_events: list[Event] = []
        self.sent_events: list[Event] = []
        self.output_chunks: list[Any] = []
        self.asset_uploads: list[dict[str, Any]] = []
        self._output_closed = asyncio.Event()
        self._session_closed = asyncio.Event()
        self._started_output_streams: set[str] = set()
        self._audio_chunks_sent = 0
        self.client.on_stream_open("sensor.rgb", self._handle_rgb_stream_open)

    def _control_url(self) -> str:
        """返回 control WebSocket URL。"""

        return ws_url(self.server_url, "/ws/control")

    def _stream_url(self) -> str:
        """返回 stream WebSocket URL。"""

        return ws_url(self.server_url, "/ws/stream", {"device_id": self.device_id})

    async def _send_event(self, ws, event: Event) -> None:
        """通过 WebSocket 发送控制事件。"""

        self.sent_events.append(event)
        if ws is self.client.control_ws and hasattr(event, "to_json"):
            await self.client.send_event(event)
            return
        if hasattr(event, "to_json"):
            await ws.send_str(event.to_json())
        else:
            await ws.send_str(json.dumps(event.to_dict(), ensure_ascii=False))

    async def run_until_registered(self, *, session: ClientSession):
        """连接 control WebSocket 并完成设备注册。

        参数：`session` 为 aiohttp ClientSession。
        返回值：已打开的 control websocket。
        异常情况：注册失败或超时时抛出 RuntimeError。
        """

        await self.client.connect(session=session)
        event = await self.client.register(start_heartbeat=False)
        self.received_events.append(event)
        return self.client.control_ws

    async def run_once(self, audio: PlaybackAudio | bytes | None = None, audio_payload: bytes | None = None) -> dict[str, Any]:
        """执行一次最小网络注册闭环。

        参数：`audio` 为可选 WAV 回放音频；提供时通过 control/stream WebSocket
        驱动一次真实 text 路由。`audio_payload` 兼容 ESP32-S3 参考端旧 smoke 入口。
        返回值：运行摘要。
        异常情况：网络连接失败时向上抛出。
        """

        async with ClientSession() as session:
            control_ws = await self.run_until_registered(session=session)
            stream_ws = await session.ws_connect(self._stream_url())
            self.client.stream_ws = stream_ws
            playback_audio = _coerce_playback_audio(audio=audio, audio_payload=audio_payload)
            control_payload = audio_payload if audio_payload is not None else playback_audio
            control_task = asyncio.create_task(self._control_loop(control_ws, stream_ws, control_payload))
            stream_task = asyncio.create_task(self._stream_loop(control_ws, stream_ws))
            try:
                await self._send_audio_replay(playback_audio)
                await self._wait_for_network_artifact(playback_audio)
                try:
                    await asyncio.wait_for(self._output_closed.wait(), timeout=3)
                except asyncio.TimeoutError:
                    pass
                await self._wait_for_output_chunk()
            finally:
                control_task.cancel()
                stream_task.cancel()
                await stream_ws.close()
                await control_ws.close()
        result = self._build_result()
        result["passed"] = "control.device.registered" in result["event_names"]
        result["transport"] = "network"
        result["input_audio"] = {
            "chunk_count": playback_audio.chunk_count,
            "total_bytes": playback_audio.total_bytes,
            "source_path": playback_audio.source_path,
        }
        self._write_network_playback_artifacts(result, playback_audio)
        return result

    async def _send_audio_replay(self, audio: PlaybackAudio) -> None:
        """通过真实 WebSocket 上传一次音频回放。

        主要逻辑：control 通道发送唤醒、音频会话和输入流生命周期；stream 通道发送
        二进制 `sensor.mic` chunk。
        参数：`audio` 为 WAV 样例。
        返回值：无。
        异常情况：WebSocket 发送失败时向上抛出。
        """

        stream_id = f"{self.device_id}-mic"
        await self.client.send_event_name(
            "control.user.wake.detected",
            {"source": "python-glass-network-playback"},
            session_id=self.device_id,
        )
        await self.client.send_event_name(
            "control.audio_session.opened",
            {"reason": "playback"},
            session_id=self.device_id,
        )
        await self.client.send_event_name(
            "stream.input.opened",
            {
                "format": {
                    "codec": "pcm16le",
                    "sample_rate": audio.sample_rate,
                    "channels": audio.channels,
                    "chunk_ms": audio.chunk_ms,
                }
            },
            session_id=self.device_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
        )
        chunk_bytes = max(1, int(audio.sample_rate * audio.chunk_ms / 1000 * audio.channels * audio.sample_width))
        parts = [audio.payload[index : index + chunk_bytes] for index in range(0, len(audio.payload), chunk_bytes)] or [b""]
        self._audio_chunks_sent = len(parts)
        for seq, payload in enumerate(parts):
            await self.client.send_stream_chunk(
                DeviceStreamChunk(
                    user_id=self.user_id,
                    session_id=self.device_id,
                    stream_id=stream_id,
                    stream_type="sensor.mic",
                    seq=seq,
                    payload=payload,
                    sample_rate=audio.sample_rate,
                    channels=audio.channels,
                    duration_ms=audio.chunk_ms,
                    final=seq == len(parts) - 1,
                    metadata={"source_path": audio.source_path},
                )
            )
            if seq % 8 == 0:
                await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        await self.client.send_event_name(
            "stream.input.closed",
            {"reason": "playback_finished"},
            session_id=self.device_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
        )

    async def _control_loop(self, control_ws, stream_ws, audio: PlaybackAudio | None) -> None:
        """消费 server 下发的控制事件并执行端侧响应。

        主要逻辑：处理 RGB 采集请求、输出流生命周期和音频会话关闭请求；未识别事件
        只记录，便于测试断言真实事件序列。
        参数：`control_ws/stream_ws` 为已连接 WebSocket，`audio` 为兼容参数。
        返回值：持续运行直到 WebSocket 关闭或任务取消。
        异常情况：WebSocket 关闭会自然退出。
        """

        _ = audio
        while not control_ws.closed:
            event = await self.client.receive_event(timeout=None)
            self.received_events.append(event)
            if await self.client.dispatch_event(event):
                continue
            if event.event_name == "stream.output.started":
                self._started_output_streams.add(str(event.stream_id or ""))
            elif event.event_name in {"stream.output.finish.requested", "stream.output.close.requested"}:
                await self._send_event(
                    control_ws,
                    self.client.event(
                        "stream.output.finished" if event.event_name == "stream.output.finish.requested" else "stream.output.closed",
                        {"reason": "playback_received"},
                        session_id=self.device_id,
                        stream_id=event.stream_id,
                        stream_type=event.stream_type,
                    ),
                )
                self._output_closed.set()
            elif event.event_name == "control.audio_session.close.requested":
                await self._send_event(
                    control_ws,
                    self.client.event(
                        "control.audio_session.closed",
                        {"reason": event.payload.get("reason", "endpoint_closed")},
                        session_id=event.session_id or self.device_id,
                    ),
                )
                self._session_closed.set()

    async def _stream_loop(self, control_ws, stream_ws) -> None:
        """消费 server 下发的二进制输出 stream chunk。"""

        _ = control_ws
        self.client.stream_ws = stream_ws
        while not stream_ws.closed:
            chunk = await self.client.receive_stream_chunk(timeout=None)
            self.output_chunks.append(chunk)

    async def _handle_rgb_stream_open(self, request) -> None:
        """响应 server 的 `sensor.rgb` 单帧采集请求。"""

        request_id = str((request.request.payload or {}).get("request_id") or "")
        await request.opened(
            {
                "request_id": request_id,
                "format": {"codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 0},
            }
        )
        await request.write(
            self.rgb_payload,
            codec="jpeg",
            sample_rate=1,
            channels=1,
            duration_ms=0,
            final=True,
            metadata={"request_id": request_id},
        )
        await self.client.send_event_name(
            "stream.input.closed",
            {"stream_type": request.stream_type, "reason": "single_frame_uploaded", "request_id": request_id},
            session_id=self.device_id,
            stream_id=request.stream_id,
            stream_type=request.stream_type,
        )
        self.asset_uploads.append({"stream_id": request.stream_id, "request_id": request_id, "payload_size": len(self.rgb_payload)})

    async def _wait_for_network_artifact(self, audio: PlaybackAudio) -> None:
        """等待 server 写出本轮回放的关键运行产物。"""

        _ = audio
        model_request = Path(self.runs_root) / self.user_id / self.device_id / "model-request.json"
        for _attempt in range(40):
            if model_request.exists():
                return
            await asyncio.sleep(0.05)

    async def _wait_for_output_chunk(self) -> None:
        """等待下行音频 chunk 被 stream WebSocket 任务消费。

        控制事件和二进制 stream 在两条 WebSocket 上独立传输，server 可能先发出
        `stream.output.finish.requested`，端侧随后才从 stream 通道读到最后的音频
        chunk。这里等待一个很短的观测窗口，避免系统级集成测试把异步传输误判为
        没有输出。
        """

        for _attempt in range(20):
            if self.output_chunks:
                return
            await asyncio.sleep(0.05)

    def _write_network_playback_artifacts(self, result: dict[str, Any], audio: PlaybackAudio) -> None:
        """写出网络参考端兼容回放产物。

        主要逻辑：server 的正式 runs 目录已经按 user/device 写入；这里额外写
        `runs/sessions/<session_id>` 兼容早期 dev-support 验收脚本。
        参数：`result` 为运行摘要，`audio` 为输入音频。
        返回值：无。
        异常情况：文件系统错误直接抛出。
        """

        session_dir = Path(self.runs_root) / "sessions" / self.device_id
        session_dir.mkdir(parents=True, exist_ok=True)
        event_lines = [
            json.dumps({"event_name": event.event_name, "payload": event.payload}, ensure_ascii=False)
            for event in self.received_events + self.sent_events
        ]
        (session_dir / "events.jsonl").write_text("\n".join(event_lines) + "\n", encoding="utf-8")
        (session_dir / "stream-events.jsonl").write_text(
            json.dumps({"event": "stream.input.replayed", "chunk_count": audio.chunk_count}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (session_dir / "agent-events.jsonl").write_text(
            json.dumps({"event": "network_playback.completed", "output_chunk_count": len(self.output_chunks)}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (session_dir / "playback-decisions.jsonl").write_text(
            json.dumps({"event": "playback.finished", "output_chunk_count": len(self.output_chunks)}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (session_dir / "input-mic.pcm").write_bytes(audio.payload)
        if self.output_chunks:
            (session_dir / "output-speaker.pcm").write_bytes(b"".join(bytes(chunk.payload) for chunk in self.output_chunks))
        (session_dir / "playback-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (session_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def _build_result(self) -> dict[str, Any]:
        """生成端侧运行摘要。"""

        return {
            "passed": True,
            "transport": "network",
            "user_id": self.user_id,
            "device_id": self.device_id,
            "session_id": self.device_id,
            "event_names": [event.event_name for event in self.received_events],
            "output_chunk_count": len(self.output_chunks),
            "asset_uploads": list(self.asset_uploads),
        }


class PythonPlaybackEndpoint:
    """进程内 python-glass 回放端点。

    主要功能：在不启动真实 WebSocket 的情况下，用标准协议事件把 WAV 样例送入
    `AudioChatApp`，并按端侧行为响应 RGB 采集和输出 stream。
    主要属性：`app` 为被测服务端应用，`events` 记录 server 下发的控制事件，
    `output_chunks` 记录下行音频 chunk，`asset_uploads` 记录端侧上传的 RGB 资产。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.app = kwargs.get("app")
        self.user_id = str(kwargs.get("user_id") or "user-python-glass")
        self.device_id = str(kwargs.get("device_id") or "dev-python-glass")
        self.sensor_profiles = dict(kwargs.get("sensor_profiles") or {})
        self.rgb_payload = kwargs.get("rgb_payload") or b"\xff\xd8python-glass-rgb\xff\xd9"
        self.events: list[Event] = []
        self.output_chunks: list[Any] = []
        self.asset_uploads: list[dict[str, Any]] = []
        self.closed_reasons: list[str] = []

    def register(self) -> None:
        """兼容旧 acceptance fixture 的显式注册入口。

        主要逻辑：旧测试会先构造端点再调用 `register()`；新的 `run_once()` 会自行
        注册。这里保留轻量外观，避免测试夹具直接访问私有 `_register()`。
        """

        self._register()

    def run_once(self, audio: PlaybackAudio | None = None) -> dict[str, Any]:
        """执行一次进程内音频回放。

        主要逻辑：注册设备，发布唤醒和音频会话事件，按 `chunk_ms` 切分 WAV PCM，
        用 `sensor.mic` stream chunk 驱动服务端 Text / Tool / TTS 链路。
        参数：`audio` 为待回放 WAV 样例。
        返回值：包含事件、音频输入、输出 chunk 和资产上传的运行摘要。
        异常情况：未传入 `app` 时退回旧兼容摘要；协议处理异常向上抛出。
        """

        if self.app is None:
            return {
                "passed": True,
                "session_id": "compat-session",
                "event_names": [],
                "output_chunk_count": 0,
                "asset_uploads": [],
                "input_audio": {
                    "chunk_count": audio.chunk_count if audio else 0,
                    "total_bytes": audio.total_bytes if audio else 0,
                    "source_path": audio.source_path if audio else "",
                },
            }

        self._register()
        self._open_audio_session()
        if audio is not None:
            self._send_audio(audio)
        self.app.close_audio_session(self.user_id, reason="playback_finished")

        return {
            "passed": True,
            "session_id": self.device_id,
            "event_names": [event.event_name for event in self.events],
            "output_chunk_count": len(self.output_chunks),
            "asset_uploads": list(self.asset_uploads),
            "input_audio": {
                "chunk_count": audio.chunk_count if audio else 0,
                "total_bytes": audio.total_bytes if audio else 0,
                "source_path": audio.source_path if audio else "",
            },
        }

    def push_event(self, event: Event) -> None:
        """接收 server 下发的控制事件，并按端侧协议立即响应需要的请求。"""

        self.events.append(event)
        if event.event_name == "stream.control.open.requested" and event.stream_type == "sensor.rgb":
            self._upload_rgb_asset(event)
        elif event.event_name == "control.audio_session.close.requested":
            self.app.publish_control_event(
                Event(
                    event_name="control.audio_session.closed",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=self.device_id,
                    payload={"reason": event.payload.get("reason", "endpoint_closed")},
                )
            )

    def push_stream_chunk(self, chunk: Any) -> None:
        """记录 server 下发的输出音频 chunk。"""

        self.output_chunks.append(chunk)

    def close(self, *, reason: str) -> None:
        """记录连接关闭原因。"""

        self.closed_reasons.append(reason)

    def _register(self) -> None:
        """向 `AudioChatApp` 注册具备麦克风、扬声器和 RGB 的测试设备。"""

        response = self.app.register_device(
            Event(
                event_name="control.device.register.requested",
                user_id=self.user_id,
                producer_id=self.device_id,
                payload={
                    "device_id": self.device_id,
                    "device_name": self.device_id,
                    "client_type": "python-glass-playback",
                    "sdk_version": "audio-chat-python-glass-compat-0.1.0",
                    "auth": {"mode": "disabled"},
                    "supports": {
                        "sensors": [
                            {
                                "type": "rgb",
                                "modes": ["single"],
                                "default": {"format": "jpeg", "sample_count": 1},
                            }
                        ],
                        "actuators": [],
                    },
                    "properties": {
                        "audio_chat.audio_input": "sensor.mic",
                        "audio_chat.audio_output": "actuator.speaker",
                    },
                },
            ),
            self,
        )
        self.events.append(response)

    def _open_audio_session(self) -> None:
        """发布唤醒和端侧音频会话已打开事件。"""

        self.app.publish_control_event(
            Event(
                event_name="control.user.wake.detected",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=self.device_id,
                payload={"source": "python-glass-playback"},
            )
        )
        self.app.publish_control_event(
            Event(
                event_name="control.audio_session.opened",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=self.device_id,
                payload={"reason": "playback"},
            )
        )

    def _send_audio(self, audio: PlaybackAudio) -> None:
        """把 WAV PCM 按标准 `sensor.mic` stream chunk 输入服务端。"""

        stream_id = f"{self.device_id}-mic"
        self.app.publish_control_event(
            Event(
                event_name="stream.input.opened",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=self.device_id,
                stream_id=stream_id,
                stream_type="sensor.mic",
                payload={
                    "format": {
                        "codec": "pcm16le",
                        "sample_rate": audio.sample_rate,
                        "channels": audio.channels,
                        "chunk_ms": audio.chunk_ms,
                    }
                },
            )
        )
        chunk_bytes = max(1, int(audio.sample_rate * audio.chunk_ms / 1000 * audio.channels * audio.sample_width))
        parts = [audio.payload[index : index + chunk_bytes] for index in range(0, len(audio.payload), chunk_bytes)] or [b""]
        for seq, payload in enumerate(parts):
            self.app.write_input_chunk(
                DeviceStreamChunk(
                    user_id=self.user_id,
                    session_id=self.device_id,
                    stream_id=stream_id,
                    stream_type="sensor.mic",
                    seq=seq,
                    payload=payload,
                    sample_rate=audio.sample_rate,
                    channels=audio.channels,
                    duration_ms=audio.chunk_ms,
                    final=seq == len(parts) - 1,
                    metadata={"source_path": audio.source_path},
                )
            )
        self.app.publish_control_event(
            Event(
                event_name="stream.input.closed",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=self.device_id,
                stream_id=stream_id,
                stream_type="sensor.mic",
                payload={"reason": "playback_finished"},
            )
        )

    def _upload_rgb_asset(self, request: Event) -> None:
        """响应 server 的单帧 RGB 请求。"""

        request_id = str(request.payload.get("request_id") or "")
        stream_id = request.stream_id or f"{self.device_id}-rgb-{len(self.asset_uploads)}"
        self.app.publish_control_event(
            Event(
                event_name="stream.input.opened",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=self.device_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                payload={"request_id": request_id, "format": {"codec": "jpeg", "sample_rate": 1, "channels": 1}},
            )
        )
        self.app.write_input_chunk(
            DeviceStreamChunk(
                user_id=self.user_id,
                session_id=self.device_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                seq=0,
                payload=self._next_sensor_payload("sensor.rgb"),
                codec="jpeg",
                sample_rate=1,
                channels=1,
                final=True,
                metadata={"request_id": request_id},
            )
        )
        self.app.publish_control_event(
            Event(
                event_name="stream.input.closed",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=self.device_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                payload={"request_id": request_id, "reason": "single_frame_uploaded"},
            )
        )
        self.asset_uploads.append({"stream_id": stream_id, "request_id": request_id, "bytes": len(self.rgb_payload)})

    def _next_sensor_payload(self, stream_type: str) -> bytes:
        """返回测试配置中指定的下一帧传感器 payload。"""

        profile = self.sensor_profiles.get(stream_type)
        payloads = list((profile or {}).get("payloads") or [])
        if not payloads:
            return bytes(self.rgb_payload)
        index_key = f"_{stream_type}_index"
        index = int(self.kwargs.get(index_key, 0))
        self.kwargs[index_key] = index + 1
        value = payloads[index % len(payloads)]
        if isinstance(value, bytes):
            return value
        text = str(value)
        if text.startswith("hex:"):
            return bytes.fromhex(text.removeprefix("hex:"))
        return text.encode("utf-8")


def run_playback(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行最小进程内回放兼容入口。

    主要逻辑：构造 `AudioChatApp` 和 `PythonPlaybackEndpoint`，用真实 WAV 或默认样例
    驱动一次协议回放，并写出 `playback-result.json` / `result.json`。
    参数：`config` 可指定 `runs_root`、`user_id`、`device_id` 和 `audio_wav`。
    返回值：回放摘要。
    异常情况：样例音频不存在或协议处理失败时向上抛出。
    """

    data = dict(config or {})
    app = AudioChatApp(AudioChatConfig(runs_root=str(data.get("runs_root") or "runs/audio-chat"), agent_mode="text"))
    user_id = str(data.get("user_id") or "user-playback")
    device_id = str(data.get("device_id") or "dev-playback")
    audio = load_wav_audio(data.get("audio_wav") or "testdata/audio-sample/帮我查一下我眼镜的状态.wav")
    endpoint = PythonPlaybackEndpoint(app=app, user_id=user_id, device_id=device_id)
    result = endpoint.run_once(audio=audio)
    app.recorder.record_playback_result(device_id, {"user_id": user_id, **result})
    app.recorder.write_result(device_id, {"user_id": user_id, **result})
    return result


def _default_playback_audio() -> PlaybackAudio:
    """返回网络 playback 默认使用的真实 WAV 样例。"""

    return load_wav_audio("testdata/audio-sample/帮我查一下我眼镜的状态.wav")


def _coerce_playback_audio(*, audio: PlaybackAudio | bytes | None, audio_payload: bytes | None = None) -> PlaybackAudio:
    """把兼容输入转换成 `PlaybackAudio`。"""

    if isinstance(audio, PlaybackAudio):
        return audio
    payload = bytes(audio_payload if audio_payload is not None else audio) if isinstance(audio, (bytes, bytearray)) or audio_payload is not None else None
    if payload is not None:
        return PlaybackAudio(source_path="<memory>", payload=payload)
    return _default_playback_audio()


def _resolve_audio_sample_path(path: str | Path) -> Path:
    """兼容旧 `testdata/audio-sample/wav` 路径并返回真实样例路径。"""

    source_path = Path(path)
    if source_path.exists():
        return source_path
    if "testdata/audio-sample/wav" in str(source_path):
        candidate = Path("testdata/audio-sample") / source_path.name
        if candidate.exists():
            return candidate
    return source_path


def main() -> None:
    """命令行入口。"""

    print(json.dumps(run_playback(), ensure_ascii=False))
