from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import ClientSession

from audio_chat_device import AudioChatDeviceClient, AudioChatEvent as Event, StreamChunk, StreamChunkCodec, new_id, ws_url


@dataclass(frozen=True)
class PlaybackAudio:
    """回放音频占位结构。

    主要功能：在缺少 python-glass 包时保留 phone mock 所需的最小兼容类型。
    """

    payload: bytes
    source_path: Path | None = None


class NetworkPythonPlaybackEndpoint:
    """phone mock 兼容用的最小网络端点基类。

    主要功能：当当前 worktree 缺少 `audio_chat_python_glass.playback` 时，为
    `NetworkPythonPhoneMockEndpoint` 提供注册、事件发送、RGB 上传和结果摘要能力。
    主要方法：`run_until_registered()`、`_open_and_send_rgb_asset()`、`run_once()`。
    异常情况：注册失败、server 未启动或 WebSocket 协议异常时抛出 RuntimeError。
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
        self.properties = dict(properties or {})
        self.supports = supports or {"sensors": [{"type": "rgb"}], "actuators": []}
        self.rgb_payload = rgb_payload or b"\xff\xd8python-phone-fallback-rgb\xff\xd9"
        self.client = AudioChatDeviceClient(
            server_url=self.server_url,
            device={
                "user_id": self.user_id,
                "device_id": self.device_id,
                "name": self.device_name,
                "device_name": self.device_name,
                "client_type": self.client_type,
                "auth": self.auth,
                "properties": self.properties,
                "supports": self.supports,
            },
        )
        self.received_events: list[Event] = []
        self.output_chunks: list[StreamChunk] = []
        self.asset_uploads: list[dict[str, Any]] = []
        self._started_output_streams: set[str] = set()
        self._output_closed = asyncio.Event()
        self._session_closed = asyncio.Event()

    def _control_url(self) -> str:
        """返回控制 WebSocket URL。"""

        return ws_url(self.server_url, "/ws/control")

    def _stream_url(self) -> str:
        """返回 stream WebSocket URL。"""

        return ws_url(self.server_url, "/ws/stream", {"device_id": self.device_id})

    async def _send_event(self, ws, event: Event) -> None:
        """发送协议事件。"""

        if ws is self.client.control_ws:
            await self.client.send_event(event)
            return
        await ws.send_str(event.to_json())

    async def run_until_registered(self, *, session: ClientSession) -> Any:
        """注册设备并返回控制 WebSocket。"""

        await self.client.connect(session=session)
        event = await self.client.register(start_heartbeat=False)
        self.received_events.append(event)
        return self.client.control_ws

    async def _open_and_send_rgb_asset(self, control_ws, stream_ws, request: Event) -> None:
        """响应 server 的 RGB 采集请求并上传一帧。"""

        stream_id = new_id("stream_rgb")
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.opened",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=self.device_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                payload={
                    "stream_type": "sensor.rgb",
                    "format": {"codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 1},
                    "request_id": request.payload.get("request_id"),
                },
            ),
        )
        await stream_ws.send_bytes(
            StreamChunkCodec.encode(
                StreamChunk(
                    user_id=self.user_id,
                    session_id=self.device_id,
                    stream_id=stream_id,
                    stream_type="sensor.rgb",
                    seq=0,
                    payload=self.rgb_payload,
                    codec="jpeg",
                    sample_rate=1,
                    channels=1,
                    duration_ms=1,
                    final=True,
                    metadata={"request_id": request.payload.get("request_id")},
                )
            )
        )
        self.asset_uploads.append({"stream_id": stream_id, "payload_size": len(self.rgb_payload)})
        await asyncio.sleep(0.02)
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.closed",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=self.device_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                payload={"stream_type": "sensor.rgb", "reason": "fallback_rgb_uploaded"},
            ),
        )

    async def _control_loop(self, control_ws, stream_ws, audio_payload: bytes | None) -> None:
        """处理最小控制事件集合。"""

        async for message in control_ws:
            if message.type.name != "TEXT":
                continue
            event = Event.from_json(message.data)
            self.received_events.append(event)
            if event.event_name == "stream.control.open.requested" and event.stream_type == "sensor.rgb":
                await self._open_and_send_rgb_asset(control_ws, stream_ws, event)

    async def _stream_loop(self, control_ws, stream_ws) -> None:
        """记录下行 stream chunk。"""

        async for message in stream_ws:
            if message.type.name != "BINARY":
                continue
            chunk = StreamChunkCodec.decode(message.data)
            self.output_chunks.append(chunk)

    async def run_once(self, audio: PlaybackAudio | None = None) -> dict[str, Any]:
        """执行一次最小网络注册闭环。"""

        async with ClientSession() as session:
            control_ws = await self.run_until_registered(session=session)
            try:
                async with session.ws_connect(self._stream_url()) as stream_ws:
                    await asyncio.sleep(0.05)
                    await stream_ws.close()
            finally:
                await control_ws.close()
        result = self._build_result()
        result["passed"] = "control.device.registered" in result["event_names"]
        return result

    def _build_result(self) -> dict[str, Any]:
        """返回运行摘要。"""

        return {
            "passed": True,
            "transport": "network",
            "user_id": self.user_id,
            "device_id": self.device_id,
            "event_names": [event.event_name for event in self.received_events],
            "output_chunk_count": len(self.output_chunks),
            "asset_uploads": list(self.asset_uploads),
        }
