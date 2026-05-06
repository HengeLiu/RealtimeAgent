from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class RingBuffer:
    max_bytes: int
    _chunks: deque[bytes] = field(default_factory=deque)
    _size: int = 0

    def push(self, data: bytes) -> None:
        self._chunks.append(data)
        self._size += len(data)
        while self._size > self.max_bytes and self._chunks:
            removed = self._chunks.popleft()
            self._size -= len(removed)

    def pop_all(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        self._size = 0
        return data


@dataclass
class Esp32AecEndpointState:
    device_id: str
    user_id: str
    mic_send_queue: deque[bytes] = field(default_factory=deque)
    aec_reference_ring: RingBuffer = field(default_factory=lambda: RingBuffer(max_bytes=16000 * 2 * 4))
    playback_ring: RingBuffer = field(default_factory=lambda: RingBuffer(max_bytes=16000 * 2 * 4))
    sensor_mic_open: bool = False
    audio_session_open: bool = False

    def registration_payload(self) -> dict:
        return {
            "device_id": self.device_id,
            "device_name": "esp32-aec-experimental",
            "client_type": "esp32-aec-experimental",
            "sdk_version": "audio-chat-endpoint-0.2.0",
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
        }

    def on_wake_detected(self) -> None:
        self.audio_session_open = True
        self.sensor_mic_open = True

    def on_dialog_closed(self) -> None:
        self.sensor_mic_open = False
        self.audio_session_open = False
        self.mic_send_queue.clear()

    def enqueue_aec_mic_pcm(self, pcm: bytes) -> None:
        if self.sensor_mic_open:
            self.mic_send_queue.append(pcm)

    def on_playback_pcm(self, pcm: bytes) -> None:
        self.playback_ring.push(pcm)
        self.aec_reference_ring.push(pcm)
