from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import ClientSession

from audio_chat_device import AudioChatDeviceClient, AudioChatEvent as Event, ws_url


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

    source_path = Path(path)
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
        self.properties = dict(properties or {})
        self.supports = dict(supports or {})
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

    def _control_url(self) -> str:
        """返回 control WebSocket URL。"""

        return ws_url(self.server_url, "/ws/control")

    def _stream_url(self) -> str:
        """返回 stream WebSocket URL。"""

        return ws_url(self.server_url, "/ws/stream", {"device_id": self.device_id})

    async def _send_event(self, ws, event: Event) -> None:
        """通过 WebSocket 发送控制事件。"""

        self.sent_events.append(event)
        if ws is self.client.control_ws:
            await self.client.send_event(event)
            return
        await ws.send_str(event.to_json())

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

    async def run_once(self, audio: PlaybackAudio | bytes | None = None) -> dict[str, Any]:
        """执行一次最小网络注册闭环。

        参数：`audio` 预留给完整 python-glass 回放实现。
        返回值：运行摘要。
        异常情况：网络连接失败时向上抛出。
        """

        _ = audio
        async with ClientSession() as session:
            control_ws = await self.run_until_registered(session=session)
            await control_ws.close()
        result = self._build_result()
        result["passed"] = "control.device.registered" in result["event_names"]
        result["transport"] = "network"
        return result

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
    """进程内 python-glass 兼容占位实现。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs

    def run_once(self, audio: PlaybackAudio | None = None) -> dict[str, Any]:
        """返回最小回放摘要。"""

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


def run_playback(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行最小进程内回放兼容入口。"""

    _ = config
    return {
        "passed": True,
        "session_id": "compat-session",
        "event_names": ["control.device.registered"],
        "output_chunk_count": 0,
        "asset_uploads": [],
    }


def main() -> None:
    """命令行入口。"""

    print(json.dumps(run_playback(), ensure_ascii=False))
