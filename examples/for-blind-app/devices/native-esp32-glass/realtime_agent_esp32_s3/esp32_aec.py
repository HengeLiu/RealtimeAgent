from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, WSMsgType

from realtime_agent_python_glass.playback import NetworkPythonPlaybackEndpoint, PlaybackAudio
from realtime_agent.protocol import Event, StreamChunk, StreamChunkCodec, StreamFormat, new_id


DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_CHUNK_MS = 20
DEFAULT_CHUNK_BYTES = DEFAULT_SAMPLE_RATE * DEFAULT_CHANNELS * 2 * DEFAULT_CHUNK_MS // 1000


@dataclass
class RingBuffer:
    """ESP32 端侧音频环形缓冲的 Python 契约模型。

    主要功能：
    1. 模拟 ESP32 AEC 试验中的 playback ring 和 reference ring。
    2. 为协议测试提供可检查的字节计数和溢出行为。

    主要属性：
    1. `max_bytes`：最多保留的字节数。
    2. `_chunks`：按写入顺序保存的音频片段。
    3. `_size`：当前有效字节数。
    """

    max_bytes: int
    _chunks: deque[bytes] = field(default_factory=deque)
    _size: int = 0

    @property
    def size(self) -> int:
        """返回当前缓冲字节数。"""

        return self._size

    def push(self, data: bytes) -> None:
        """写入音频片段并按容量淘汰早期数据。

        参数：`data` 是 PCM 或其他端侧音频字节。
        返回值：无。
        异常情况：无；空字节会被忽略。
        """

        if not data:
            return
        self._chunks.append(bytes(data))
        self._size += len(data)
        while self._size > self.max_bytes and self._chunks:
            removed = self._chunks.popleft()
            self._size -= len(removed)

    def pop_all(self) -> bytes:
        """取出并清空当前所有缓冲字节。"""

        data = b"".join(self._chunks)
        self._chunks.clear()
        self._size = 0
        return data


@dataclass
class Esp32S3EndpointConfig:
    """ESP32-S3 参考端本地配置。

    主要功能：
    1. 承接 `realtime-agent.config.sync` 生成的 `.env`。
    2. 避免固件或 bridge 手写与 server 不一致的 user/device/token。

    主要属性：server URL、user_id、device_id、auth、音频格式和 AEC 参数。
    """

    server_url: str = "http://127.0.0.1:8765"
    user_id: str = "user-endpoint-001"
    device_id: str = "dev-esp32-s3-001"
    auth_token: str = ""
    auth_mode: str = "disabled"
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS
    chunk_ms: int = DEFAULT_CHUNK_MS
    wake_word_mode: str = "endpoint"
    aec_mode: str = "endpoint"
    playback_reference: str = "endpoint_ring_buffer"
    phone_camera_sink_ws_uri: str = ""
    phone_camera_stream_interval_ms: int = 500

    @classmethod
    def from_env_file(cls, path: str | Path) -> "Esp32S3EndpointConfig":
        """从 `.env` 文件读取 ESP32-S3 参考端配置。

        参数：`path` 是 `realtime-agent.config.sync` 生成或开发者手写的 env 文件。
        返回值：`Esp32S3EndpointConfig`。
        异常情况：文件不存在时由 `Path.read_text` 抛出异常。
        """

        values: dict[str, str] = {}
        for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        auth_token = values.get("REALTIME_AGENT_AUTH_TOKEN", "")
        return cls(
            server_url=values.get("REALTIME_AGENT_SERVER_URL", cls.server_url),
            user_id=values.get("REALTIME_AGENT_USER_ID", cls.user_id),
            device_id=values.get("REALTIME_AGENT_DEVICE_ID", cls.device_id),
            auth_token=auth_token,
            auth_mode=values.get("REALTIME_AGENT_AUTH_MODE", "static_token" if auth_token else "disabled"),
            sample_rate=int(values.get("REALTIME_AGENT_AUDIO_SAMPLE_RATE", DEFAULT_SAMPLE_RATE)),
            channels=int(values.get("REALTIME_AGENT_AUDIO_CHANNELS", DEFAULT_CHANNELS)),
            chunk_ms=int(values.get("REALTIME_AGENT_AUDIO_CHUNK_MS", DEFAULT_CHUNK_MS)),
            wake_word_mode=values.get("REALTIME_AGENT_WAKE_WORD_MODE", "endpoint"),
            aec_mode=values.get("REALTIME_AGENT_AEC_MODE", "endpoint"),
            playback_reference=values.get("REALTIME_AGENT_PLAYBACK_REFERENCE", "endpoint_ring_buffer"),
            phone_camera_sink_ws_uri=values.get("REALTIME_AGENT_PHONE_CAMERA_SINK_WS_URI", ""),
            phone_camera_stream_interval_ms=int(values.get("REALTIME_AGENT_PHONE_CAMERA_STREAM_INTERVAL_MS", 500)),
        )

    def auth_payload(self) -> dict[str, str]:
        """生成注册事件使用的 auth payload。"""

        if self.auth_mode == "static_token":
            return {"mode": "static_token", "token": self.auth_token}
        if self.auth_mode == "signed_token":
            return {"mode": "signed_token", "token": self.auth_token}
        return {"mode": "disabled"}


@dataclass
class Esp32AecEndpointState:
    """ESP32-S3 realtime-agent 端侧参考状态机。

    主要功能：
    1. 固化注册 properties/supports。
    2. 确保 wake 前不会上传 `sensor.mic`。
    3. 跟踪 speaker 下行、playback ring 和 AEC reference ring。
    4. 输出真机联调需要的诊断摘要。

    主要方法：`registration_payload()`、`on_wake_detected()`、
    `on_audio_session_open_requested()`、`enqueue_aec_mic_pcm()`、
    `on_playback_pcm()` 和 `diagnostics()`。
    """

    device_id: str
    user_id: str
    auth: dict[str, str] = field(default_factory=lambda: {"mode": "disabled"})
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS
    chunk_ms: int = DEFAULT_CHUNK_MS
    wake_word_mode: str = "endpoint"
    aec_mode: str = "endpoint"
    playback_reference: str = "endpoint_ring_buffer"
    rgb_capture_enabled: bool = True
    rgb_payload: bytes = b"\xff\xd8esp32-s3-reference-rgb\xff\xd9"
    phone_camera_sink_ws_uri: str = ""
    phone_camera_stream_interval_ms: int = 500
    mic_send_queue: deque[bytes] = field(default_factory=deque)
    aec_reference_ring: RingBuffer = field(default_factory=lambda: RingBuffer(max_bytes=DEFAULT_SAMPLE_RATE * 2 * 4))
    playback_ring: RingBuffer = field(default_factory=lambda: RingBuffer(max_bytes=DEFAULT_SAMPLE_RATE * 2 * 4))
    sensor_mic_open: bool = False
    audio_session_open: bool = False
    wake_detected: bool = False
    session_id: str | None = None
    input_stream_id: str | None = None
    close_reason: str | None = None
    mic_chunks_sent: int = 0
    mic_bytes_sent: int = 0
    speaker_chunks_received: int = 0
    speaker_bytes_received: int = 0
    stream_open_count: int = 0
    playback_started_count: int = 0
    playback_finished_count: int = 0
    playback_failed_count: int = 0
    rgb_capture_requests: int = 0
    rgb_frames_sent: int = 0
    rgb_bytes_sent: int = 0
    direct_rgb_frames_sent: int = 0
    direct_rgb_bytes_sent: int = 0
    direct_camera_errors: int = 0
    control_events_received: int = 0
    last_error_phase: str | None = None

    @classmethod
    def from_config(cls, config: Esp32S3EndpointConfig) -> "Esp32AecEndpointState":
        """按本地配置创建端侧状态机。"""

        return cls(
            device_id=config.device_id,
            user_id=config.user_id,
            auth=config.auth_payload(),
            sample_rate=config.sample_rate,
            channels=config.channels,
            chunk_ms=config.chunk_ms,
            wake_word_mode=config.wake_word_mode,
            aec_mode=config.aec_mode,
            playback_reference=config.playback_reference,
            phone_camera_sink_ws_uri=config.phone_camera_sink_ws_uri,
            phone_camera_stream_interval_ms=config.phone_camera_stream_interval_ms,
        )

    def registration_payload(self) -> dict[str, Any]:
        """生成 `control.device.register.requested` 的 payload。"""

        stream_format = self.stream_format()
        return {
            "device_id": self.device_id,
            "name": "ESP32-S3 设备示例",
            "device_name": "esp32-s3-aec-reference",
            "client_type": "esp32-s3",
            "sdk_version": "realtime-agent-endpoint-0.3.0",
            "auth": dict(self.auth),
            "properties": {
                "realtime_agent.audio_input": "sensor.mic",
                "realtime_agent.audio_output": "actuator.speaker",
                "audio.wake_word": self.wake_word_mode,
                "audio.aec": self.aec_mode,
                "audio.playback_reference": self.playback_reference,
                "audio.input": stream_format.__dict__,
                "audio.output": stream_format.__dict__,
                "sensor.rgb.format": {"codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 1},
                "direct.camera_source": True,
                "direct.camera.frame_format": "realtime_agent.direct_frame.v1",
                "direct.camera.default_sink_uri": self.phone_camera_sink_ws_uri,
                "direct.camera.stream_interval_ms": self.phone_camera_stream_interval_ms,
            },
            "supports": {
                "sensors": [
                    {
                        "type": "rgb",
                        "modes": ["single"],
                        "default": {"format": "jpeg", "frequency_hz": 1, "sample_count": 1},
                        "external": {"direct_camera_sink_ws_uri": self.phone_camera_sink_ws_uri},
                    }
                ]
            },
        }

    def stream_format(self) -> StreamFormat:
        """返回 ESP32-S3 输入/输出音频格式。"""

        return StreamFormat(
            codec="pcm16le",
            sample_rate=self.sample_rate,
            channels=self.channels,
            chunk_ms=self.chunk_ms,
        )

    def on_wake_detected(self) -> None:
        """记录端侧唤醒，但不立即打开麦克风 stream。

        主要逻辑：ESP32 启动后只保持 control 连接；本地 wake 命中后先发
        `control.user.wake.detected`，收到 server 的 audio session open 请求后才上传音频。
        """

        self.wake_detected = True
        self.close_reason = None

    def on_audio_session_open_requested(self, session_id: str | None, *, stream_id: str | None = None) -> str:
        """处理 server 下发的 audio session open 请求并打开 mic stream。"""

        self.control_events_received += 1
        self.session_id = session_id or self.device_id
        self.input_stream_id = stream_id or new_id("stream_mic")
        self.audio_session_open = True
        self.sensor_mic_open = True
        self.stream_open_count += 1
        return self.input_stream_id

    def on_audio_session_close_requested(self, reason: str = "server_requested") -> None:
        """处理 server 下发的 audio session close 请求。"""

        self.control_events_received += 1
        self.sensor_mic_open = False
        self.audio_session_open = False
        self.close_reason = reason
        self.mic_send_queue.clear()

    def on_interrupt_detected(self) -> None:
        """记录端侧用户打断，不默认关闭整个会话。"""

        self.control_events_received += 1
        self.playback_started_count = 0

    def enqueue_aec_mic_pcm(self, pcm: bytes) -> bool:
        """写入端侧 AEC 后麦克风 PCM 队列。

        参数：`pcm` 是端侧完成 AEC/NS/AGC 后的 PCM16LE 字节。
        返回值：是否成功入队。
        异常情况：无；会话未打开时拒绝入队并记录诊断阶段。
        """

        if not self.sensor_mic_open:
            self.last_error_phase = "sensor_mic_not_open"
            return False
        payload = bytes(pcm)
        if not payload:
            return False
        self.mic_send_queue.append(payload)
        self.mic_chunks_sent += 1
        self.mic_bytes_sent += len(payload)
        return True

    def on_playback_pcm(self, pcm: bytes) -> None:
        """处理 server 下行 speaker PCM。

        主要逻辑：speaker chunk 先进入播放缓冲；实际写播放器时同一帧写入
        AEC reference ring。Python 模型用同步写入表示这一约束。
        """

        payload = bytes(pcm)
        if not payload:
            return
        self.speaker_chunks_received += 1
        self.speaker_bytes_received += len(payload)
        self.playback_ring.push(payload)
        self.aec_reference_ring.push(payload)

    def mark_playback_started(self) -> None:
        """记录端侧开始播放一个 output stream。"""

        self.playback_started_count += 1

    def mark_playback_finished(self) -> None:
        """记录端侧完成播放。"""

        self.playback_finished_count += 1

    def mark_playback_failed(self, phase: str) -> None:
        """记录端侧播放失败阶段。"""

        self.playback_failed_count += 1
        self.last_error_phase = phase

    def on_rgb_configure_requested(self, payload: dict[str, Any] | None = None) -> bytes | None:
        """处理 server 下发的 `sensor.rgb` 抓拍或配置请求。

        功能：
        1. 记录 RGB 请求次数，方便真机 smoke 对照串口日志。
        2. 在 Python 参考端中返回一帧 JPEG；真实固件应在这里触发摄像头抓拍。
        3. 摄像头不可用时返回 `None` 并写入诊断阶段。

        主要逻辑：
        1. `mode=stop` 只记录配置事件，不上传新帧。
        2. 非 stop 请求返回 `rgb_payload`，大字节后续必须走 stream。

        参数：
        1. `payload`：`stream.control.open.requested` 的 payload。

        返回值：
        1. JPEG bytes；`None` 表示失败；空 bytes 表示 stop 不上传。

        异常情况：
        1. 不抛出异常；失败原因写入 `last_error_phase`。
        """

        self.control_events_received += 1
        self.rgb_capture_requests += 1
        payload = payload or {}
        sink_uri = (
            payload.get("phone_camera_sink_ws_uri")
            or payload.get("direct_camera_sink_uri")
            or payload.get("camera_sink_ws_uri")
            or payload.get("sink_uri")
        )
        if sink_uri:
            self.phone_camera_sink_ws_uri = str(sink_uri)
        if not self.rgb_capture_enabled:
            self.last_error_phase = "sensor_rgb_unavailable"
            return None
        if str(payload.get("mode") or "single") == "stop":
            return b""
        frame = bytes(self.rgb_payload)
        self.rgb_frames_sent += 1
        self.rgb_bytes_sent += len(frame)
        return frame

    def encode_direct_rgb_frame(
        self,
        *,
        stream_id: str,
        seq: int,
        payload: bytes,
        timestamp_ms: int | None = None,
    ) -> bytes:
        """编码 ESP32 到手机直连相机帧。

        功能：让 ESP32 示例可以把 JPEG 直接推给 iOS phone 的相机接收服务。

        主要逻辑：
        1. 前 4 字节是大端 JSON header 长度。
        2. header 使用 realtime-agent 字段描述 stream、序号、时间戳、编码和 payload 大小。
        3. header 后面直接拼接 JPEG payload。

        参数：
        1. `stream_id`：本次直连相机 stream 标识。
        2. `seq`：帧序号。
        3. `payload`：JPEG 字节。
        4. `timestamp_ms`：可选毫秒时间戳；为空时使用当前时间。

        返回值：可直接作为 WebSocket binary frame 发送的 bytes。
        异常情况：JSON 编码失败时由 `json.dumps` 抛出异常。
        """

        header = {
            "stream_type": "sensor.rgb",
            "stream_id": stream_id,
            "seq": seq,
            "timestamp_ms": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
            "codec": "jpeg",
            "payload_size": len(payload),
        }
        header_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return len(header_bytes).to_bytes(4, "big") + header_bytes + bytes(payload)

    def mark_direct_camera_sent(self, payload_size: int) -> None:
        """记录 ESP32 已经向手机直连发送一帧相机数据。"""

        self.direct_rgb_frames_sent += 1
        self.direct_rgb_bytes_sent += payload_size

    def mark_direct_camera_failed(self, phase: str) -> None:
        """记录 ESP32 到手机直连相机发送失败。"""

        self.direct_camera_errors += 1
        self.last_error_phase = phase

    def diagnostics(self) -> dict[str, Any]:
        """输出真机联调和 contract test 共用的诊断摘要。"""

        return {
            "device_id": self.device_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "wake_detected": self.wake_detected,
            "audio_session_open": self.audio_session_open,
            "sensor_mic_open": self.sensor_mic_open,
            "input_stream_id": self.input_stream_id,
            "stream_open_count": self.stream_open_count,
            "mic_chunks_sent": self.mic_chunks_sent,
            "mic_bytes_sent": self.mic_bytes_sent,
            "speaker_chunks_received": self.speaker_chunks_received,
            "speaker_bytes_received": self.speaker_bytes_received,
            "playback_ring_bytes": self.playback_ring.size,
            "aec_reference_bytes": self.aec_reference_ring.size,
            "playback_started_count": self.playback_started_count,
            "playback_finished_count": self.playback_finished_count,
            "playback_failed_count": self.playback_failed_count,
            "rgb_capture_requests": self.rgb_capture_requests,
            "rgb_frames_sent": self.rgb_frames_sent,
            "rgb_bytes_sent": self.rgb_bytes_sent,
            "direct_camera_sink_ws_uri": self.phone_camera_sink_ws_uri,
            "direct_rgb_frames_sent": self.direct_rgb_frames_sent,
            "direct_rgb_bytes_sent": self.direct_rgb_bytes_sent,
            "direct_camera_errors": self.direct_camera_errors,
            "close_reason": self.close_reason,
            "last_error_phase": self.last_error_phase,
            "audio_format": self.stream_format().__dict__,
            "aec_mode": self.aec_mode,
            "playback_reference": self.playback_reference,
        }


class NetworkEsp32S3Endpoint(NetworkPythonPlaybackEndpoint):
    """按真实网络协议运行的 ESP32-S3 参考端。

    主要功能：
    1. 通过 `/ws/control` 注册 properties 和 supports。
    2. 本地 wake 后等待 `control.audio_session.open.requested`。
    3. 打开 `/ws/stream` 上传 `sensor.mic` PCM chunk。
    4. 消费 `actuator.speaker` 下行 chunk，并回报 started/finished/closed。
    """

    def __init__(
        self,
        *,
        config: Esp32S3EndpointConfig | None = None,
        server_url: str | None = None,
        user_id: str | None = None,
        device_id: str | None = None,
        runs_root: str = "runs/realtime-agent",
        auth: dict[str, Any] | None = None,
    ) -> None:
        endpoint_config = config or Esp32S3EndpointConfig()
        if server_url is not None:
            endpoint_config.server_url = server_url
        if user_id is not None:
            endpoint_config.user_id = user_id
        if device_id is not None:
            endpoint_config.device_id = device_id
        if auth is not None:
            endpoint_config.auth_mode = str(auth.get("mode", "disabled"))
            endpoint_config.auth_token = str(auth.get("token", ""))
        self.state = Esp32AecEndpointState.from_config(endpoint_config)
        payload = self.state.registration_payload()
        super().__init__(
            server_url=endpoint_config.server_url,
            user_id=endpoint_config.user_id,
            device_id=endpoint_config.device_id,
            runs_root=runs_root,
            auth=dict(payload["auth"]),
            device_name=str(payload["device_name"]),
            client_type=str(payload["client_type"]),
            properties=dict(payload["properties"]),
            supports=dict(payload["supports"]),
        )

    async def _control_loop(self, control_ws, stream_ws, audio_payload: bytes | None) -> None:
        async for message in control_ws:
            if message.type != WSMsgType.TEXT:
                continue
            event = Event.from_dict(json.loads(message.data))
            self.received_events.append(event)
            if event.event_name == "control.audio_session.open.requested":
                self._session_id = self.device_id
                self.state.on_audio_session_open_requested(self.device_id)
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="control.audio_session.opened",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        payload={"reason": "esp32_session_opened", "aec": self.state.aec_mode},
                    ),
                )
                await self._open_and_send_mic(control_ws, stream_ws, self.device_id, audio_payload)
            elif event.event_name in {"stream.output.finish.requested", "stream.output.close.requested"}:
                self.state.mark_playback_finished()
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.finished",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        stream_id=event.stream_id,
                        stream_type=event.stream_type,
                        payload={"stream_type": event.stream_type, "diagnostics": self.state.diagnostics()},
                    ),
                )
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.closed",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        stream_id=event.stream_id,
                        stream_type=event.stream_type,
                        payload={"stream_type": event.stream_type, "reason": "esp32_playback_closed"},
                    ),
                )
                self._output_closed.set()
            elif event.event_name == "stream.output.cancel.requested":
                self.state.on_interrupt_detected()
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.cancelled",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        stream_id=event.stream_id,
                        stream_type=event.stream_type,
                        payload={"stream_type": event.stream_type, "reason": "esp32_user_interrupt"},
                    ),
                )
            elif event.event_name == "control.audio_session.close.requested":
                self.state.on_audio_session_close_requested(event.payload.get("reason", "server_requested"))
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="control.audio_session.closed",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        payload={"reason": "esp32_session_closed", "diagnostics": self.state.diagnostics()},
                    ),
                )
                self._session_closed.set()
            elif event.event_name == "stream.control.open.requested" and event.stream_type == "sensor.rgb":
                await self._open_and_send_rgb_asset(control_ws, stream_ws, event)

    async def _stream_loop(self, control_ws, stream_ws) -> None:
        async for message in stream_ws:
            if message.type != WSMsgType.BINARY:
                continue
            chunk = StreamChunkCodec.decode(message.data)
            if chunk.stream_type != "actuator.speaker":
                continue
            if chunk.stream_id not in self._started_output_streams:
                self._started_output_streams.add(chunk.stream_id)
                self.state.mark_playback_started()
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.started",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        stream_id=chunk.stream_id,
                        stream_type=chunk.stream_type,
                        payload={"stream_type": chunk.stream_type},
                    ),
                )
            self.state.on_playback_pcm(chunk.payload)
            self.output_chunks.append(chunk)

    async def _open_and_send_mic(self, control_ws, stream_ws, session_id: str | None, audio_payload: bytes | PlaybackAudio | None) -> None:
        stream_id = self.state.input_stream_id or new_id("stream_mic")
        self._input_stream_id = stream_id
        stream_format = self.state.stream_format()
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.opened",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=session_id,
                stream_id=stream_id,
                stream_type="sensor.mic",
                payload={
                    "stream_type": "sensor.mic",
                    "format": stream_format.__dict__,
                    "aec": self.state.aec_mode,
                },
            ),
        )
        if isinstance(audio_payload, PlaybackAudio):
            payloads = audio_payload.chunks or [b""]
        else:
            payloads = [audio_payload if audio_payload is not None else b"\x00" * DEFAULT_CHUNK_BYTES]
        for payload in payloads:
            self.state.enqueue_aec_mic_pcm(payload)
        seq = 0
        while self.state.mic_send_queue:
            chunk_payload = self.state.mic_send_queue.popleft()
            await stream_ws.send_bytes(
                StreamChunkCodec.encode(
                    StreamChunk(
                        user_id=self.user_id,
                        session_id=session_id or "",
                        stream_id=stream_id,
                        stream_type="sensor.mic",
                        seq=seq,
                        payload=chunk_payload,
                        final=not self.state.mic_send_queue,
                        codec=stream_format.codec,
                        sample_rate=stream_format.sample_rate,
                        channels=stream_format.channels,
                        duration_ms=stream_format.chunk_ms,
                        metadata={"aec": self.state.aec_mode},
                    )
                )
            )
            seq += 1
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
                payload={"stream_type": "sensor.mic", "reason": "esp32_input_done"},
            ),
        )

    async def _open_and_send_rgb_asset(self, control_ws, stream_ws, event: Event) -> None:
        """按 `sensor.rgb` 控制事件上传一帧 JPEG 资产。

        功能：
        1. 模拟 ESP32-S3 收到抓拍或采样配置后的端侧行为。
        2. 使用 `/ws/stream` 上传图片字节，避免在控制事件 payload 中塞媒体大字节。

        参数：
        1. `control_ws`：控制 WebSocket。
        2. `stream_ws`：stream WebSocket。
        3. `event`：server 下发的 `stream.control.open.requested`。

        返回值：
        1. 无。

        异常情况：
        1. WebSocket 写入失败时由 aiohttp 向上传递。
        """

        frame = self.state.on_rgb_configure_requested(dict(event.payload))
        if frame is None:
            await self._send_event(
                control_ws,
                Event(
                    event_name="stream.input.failed",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=self.device_id,
                    stream_type="sensor.rgb",
                    payload={"stream_type": "sensor.rgb", "reason": self.state.last_error_phase},
                ),
            )
            return
        if frame == b"":
            return
        request_id = str(event.payload.get("request_id") or "")
        correlation_id = str(event.payload.get("correlation_id") or "")
        stream_id = new_id("stream_rgb")
        session_id = event.session_id or self._session_id or self.device_id
        metadata: dict[str, Any] = {}
        if request_id:
            metadata["request_id"] = request_id
        if correlation_id:
            metadata["correlation_id"] = correlation_id
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.opened",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=session_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                payload={
                    "stream_type": "sensor.rgb",
                    "format": {"codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 1},
                },
            ),
        )
        direct_sent = await self._send_direct_rgb_frame(frame, stream_id=stream_id, seq=0)
        await stream_ws.send_bytes(
            StreamChunkCodec.encode(
                StreamChunk(
                    user_id=self.user_id,
                    session_id=session_id,
                    stream_id=stream_id,
                    stream_type="sensor.rgb",
                    seq=0,
                    payload=frame,
                    codec="jpeg",
                    sample_rate=1,
                    channels=1,
                    duration_ms=1,
                    final=True,
                    metadata=metadata,
                )
            )
        )
        await asyncio.sleep(0.05)
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.closed",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=session_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                payload={"stream_type": "sensor.rgb", "reason": "esp32_rgb_uploaded"},
            ),
        )
        self.asset_uploads.append(
            {
                "stream_id": stream_id,
                "request_id": request_id,
                "payload_size": len(frame),
                "direct_camera_sent": direct_sent,
            }
        )

    async def _send_direct_rgb_frame(self, frame: bytes, *, stream_id: str, seq: int) -> bool:
        """把 JPEG 帧按 realtime-agent 直连格式发送给 phone 相机接收服务。

        功能：当 `REALTIME_AGENT_PHONE_CAMERA_SINK_WS_URI` 已配置时，ESP32 参考端会额外
        建立一条到 phone 的 WebSocket，把同一帧图片直接推给手机端。
        主要逻辑：server 资产上传仍然保留，直连发送失败只记录诊断，不影响
        `sensor.rgb` stream 上传。
        参数：`frame` 为 JPEG bytes，`stream_id` 和 `seq` 用于直连帧 header。
        返回值：发送成功返回 True；未配置或失败返回 False。
        异常情况：网络错误被捕获并写入 `last_error_phase`。
        """

        if not self.state.phone_camera_sink_ws_uri:
            return False
        from aiohttp import ClientSession

        payload = self.state.encode_direct_rgb_frame(stream_id=stream_id, seq=seq, payload=frame)
        try:
            async with ClientSession() as session:
                async with session.ws_connect(self.state.phone_camera_sink_ws_uri) as ws:
                    await ws.send_bytes(payload)
            self.state.mark_direct_camera_sent(len(frame))
            return True
        except Exception:
            self.state.mark_direct_camera_failed("direct_camera_send_failed")
            return False

    async def run_once(self, audio_payload: bytes | None = None) -> dict[str, Any]:
        """执行一次 ESP32-S3 网络协议 smoke。"""

        self.state.on_wake_detected()
        async with ClientSession() as session:
            control_ws = await self.run_until_registered(session=session)
            stream_ws = await session.ws_connect(self._stream_url())
            self.client.stream_ws = stream_ws
            control_task = asyncio.create_task(self._control_loop(control_ws, stream_ws, audio_payload))
            stream_task = asyncio.create_task(self._stream_loop(control_ws, stream_ws))
            try:
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="control.user.wake.detected",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        payload={"source": "esp32-s3-wake-word"},
                    ),
                )
                try:
                    await asyncio.wait_for(self._output_closed.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                if not self._session_closed.is_set():
                    self.state.on_audio_session_close_requested("esp32_smoke_completed")
                    await self._send_event(
                        control_ws,
                        Event(
                            event_name="control.audio_session.closed",
                            user_id=self.user_id,
                            producer_id=self.device_id,
                            session_id=self.device_id,
                            payload={"reason": "esp32_smoke_completed", "diagnostics": self.state.diagnostics()},
                        ),
                    )
                    self._session_closed.set()
                try:
                    await asyncio.wait_for(self._session_closed.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass
            finally:
                control_task.cancel()
                stream_task.cancel()
                await stream_ws.close()
                await control_ws.close()
        result = self._build_result()
        self._write_network_playback_artifacts(
            result,
            PlaybackAudio(source_path="<memory>", payload=bytes(audio_payload or b"")),
        )
        return result

    def _build_result(self) -> dict[str, Any]:
        result = super()._build_result()
        result["event_names"] = [event.event_name for event in self.received_events + self.sent_events]
        result["endpoint"] = "esp32-s3"
        result["diagnostics"] = self.state.diagnostics()
        result["properties"] = dict(self.properties)
        event_names = set(result.get("event_names", []))
        core_events_passed = {
            "control.device.registered",
            "control.audio_session.open.requested",
            "control.audio_session.opened",
            "stream.input.opened",
            "stream.output.open.requested",
            "stream.output.started",
            "stream.output.finish.requested",
            "stream.output.finished",
            "stream.output.closed",
            "control.audio_session.closed",
        }.issubset(event_names)
        result["passed"] = bool(
            core_events_passed
            and result["diagnostics"]["mic_chunks_sent"] > 0
            and result["diagnostics"]["speaker_chunks_received"] > 0
            and result["diagnostics"]["aec_reference_bytes"] == result["diagnostics"]["speaker_bytes_received"]
        )
        return result


async def run_network_esp32_s3(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """按配置运行一次 ESP32-S3 网络参考端闭环。"""

    config = config or {}
    endpoint_config = Esp32S3EndpointConfig(
        server_url=config.get("server_url", "http://127.0.0.1:8765"),
        user_id=config.get("user_id", "user-esp32-s3-001"),
        device_id=config.get("device_id", "dev-esp32-s3-001"),
        auth_token=str((config.get("auth") or {}).get("token", "")),
        auth_mode=str((config.get("auth") or {}).get("mode", "disabled")),
        phone_camera_sink_ws_uri=str(
            config.get("phone_camera_sink_ws_uri") or config.get("direct_camera_sink_uri") or ""
        ),
        phone_camera_stream_interval_ms=int(config.get("phone_camera_stream_interval_ms", 500)),
    )
    endpoint = NetworkEsp32S3Endpoint(
        config=endpoint_config,
        runs_root=config.get("runs_root", "runs/realtime-agent"),
    )
    if config.get("mode") in {"register_only", "network_register"}:
        from aiohttp import ClientSession

        async with ClientSession() as session:
            control_ws = await endpoint.run_until_registered(session=session)
            try:
                await asyncio.sleep(float(config.get("hold_seconds", 0.05)))
            finally:
                await control_ws.close()
        result = endpoint._build_result()
        result["passed"] = "control.device.registered" in result["event_names"]
        result["mode"] = "register_only"
        return result
    return await endpoint.run_once()
